"""Blueprint: conversation (SSE streaming) and chart serving."""
import json
import logging
import os
import re
import time
import uuid
from ast import literal_eval

from flask import Blueprint, g, request, Response, jsonify

from .state import session_manager, config_manager, chart_store, check_session_ownership
from agent.activation import ActivationContext, INTERNAL_ACTIONS
from agent.agent import BusinessAgent
from agent.commands import CommandLoader, CommandType
try:
    from agent.memory import (
        maybe_schedule_consolidation as _maybe_schedule_memory_consolidation,
        schedule_extraction as _schedule_memory_extraction,
    )
except ImportError:
    _maybe_schedule_memory_consolidation = None   # type: ignore[assignment]
    _schedule_memory_extraction = None            # type: ignore[assignment]
from agent.prompts import get_system_prompt
from agent.reasoning import split_reasoning_tags
from agent.retry import call_with_retry as _call_with_retry
from agent.skills import SkillLoader
from infrastructure.artifact_lifecycle import register_artifact

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)


# === CUSTOM MODIFICATION START - 默认数据源自动连接（支持多数据源） ===
def _auto_connect_default_datasource(session_id: str):
    """在新会话创建时自动连接配置的默认数据源（支持多个）"""
    import json
    from pathlib import Path
    
    # 读取配置文件
    config_file = Path(__file__).parent.parent / "config" / "default_datasource.json"
    if not config_file.exists():
        log.debug("[auto-connect] config file not found: %s", config_file)
        return
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # 检查是否启用
        if not config.get("enabled", False):
            log.debug("[auto-connect] disabled in config")
            return
        
        sess = session_manager.get(session_id)
        if not sess:
            log.warning("[auto-connect] session not found: %s", session_id)
            return
        
        # 支持单个数据源（向后兼容）
        if "connection_string" in config:
            _connect_single_datasource(sess, session_id, config)
        
        # 支持多个数据源
        datasources = config.get("datasources", [])
        if datasources:
            success_count = 0
            first_source_id = None  # 记录第一个成功连接的数据源
            
            for idx, ds_config in enumerate(datasources):
                # 检查是否启用该数据源
                if not ds_config.get("enabled", True):
                    log.debug("[auto-connect] datasource #%d disabled, skip", idx)
                    continue
                
                try:
                    source_id = _connect_single_datasource(sess, session_id, ds_config)
                    log.debug("[auto-connect] datasource #%d returned source_id: %s", idx, source_id)
                    
                    if source_id is not None:
                        success_count += 1
                        # add_source已经自动激活了，无需额外操作
                        log.debug("[auto-connect] datasource #%d activated: %s", idx, source_id)
                except Exception as exc:
                    log.error("[auto-connect] datasource #%d FAILED: %s", idx, exc)
            
            log.info("[auto-connect] connected %d/%d datasources for sid=%s (all auto-activated)", 
                     success_count, len(datasources), session_id)
            
            log.info("[auto-connect] connected %d/%d datasources for sid=%s", 
                     success_count, len(datasources), session_id)
            
    except Exception as exc:
        log.error("[auto-connect] FAILED  sid=%s: %s", session_id, exc)
        raise


def _connect_single_datasource(sess, session_id: str, config: dict):
    """连接单个数据源的辅助函数"""
    from data.connector import SQLDataSource
    
    conn_type = config.get("type", "sql")
    conn_str = config.get("connection_string", "").strip()
    display_name = config.get("display_name", "默认数据源")
    
    if not conn_str:
        log.warning("[auto-connect] connection_string is empty for '%s'", display_name)
        return
    
    # 连接数据源
    if conn_type == "sql":
        source = SQLDataSource(conn_str, display_name)
        
        # === CUSTOM MODIFICATION START - 智能表选择配置 ===
        # 保存配置到数据源对象
        auto_select = config.get("auto_select_tables", False)
        select_all = config.get("select_all_tables", False)
        max_tables = config.get("max_tables", 20)
        table_mapping = config.get("table_mapping", {})
        default_tables = config.get("default_tables", [])
        
        if auto_select:
            source._auto_select_enabled = True
            source._select_all_tables = select_all
            source._max_tables = max_tables
            source._max_tables_limit = max_tables  # 设置表数量限制
            source._table_mapping = table_mapping
            source._default_tables = default_tables
            
            log.debug("[auto-connect] enabled auto table selection for '%s', select_all=%s, max=%d",
                     display_name, select_all, max_tables)
            
            # 如果启用了"选择所有表"，立即设置
            if select_all:
                try:
                    all_tables = source._all_table_names()
                    # 限制表数量
                    tables_to_select = all_tables[:max_tables]
                    if tables_to_select:
                        source.set_analysis_tables(tables_to_select)
                        log.info("[auto-connect] selected all tables for '%s': %d tables (max: %d)",
                                display_name, len(tables_to_select), max_tables)
                    else:
                        log.warning("[auto-connect] no tables found in '%s'", display_name)
                except Exception as exc:
                    log.warning("[auto-connect] failed to select all tables for '%s': %s",
                               display_name, exc)
        # === CUSTOM MODIFICATION END ===
        
        source_id = sess.add_source(source)
        log.info("[auto-connect] SUCCESS  sid=%s  source='%s'  source_id=%s", 
                 session_id, display_name, source_id)
        return source_id  # === 新增：返回source_id ===
    else:
        log.warning("[auto-connect] unsupported type: %s for '%s'", conn_type, display_name)
        return None  # === 新增：返回None ===
# === CUSTOM MODIFICATION END ===


_PROMPT_SUGGESTION_DIRECTIVE = """You are a prompt suggestion engine.
Predict the single next message this user is most likely to type after reading the assistant's latest answer.
Return only the message text that should be prefilled in the chat input.
Do not explain, do not quote, do not use markdown, and do not mention that this is a suggestion.
Keep it short, concrete, and directly actionable."""


def format_feishu_ask_user(event: dict) -> str:
    """Render a Web-only choice card as a replyable Feishu text message."""
    question = str(event.get("question") or "请告诉我下一步希望继续处理什么。 ").strip()
    raw_options = event.get("options")
    if isinstance(raw_options, str):
        try:
            raw_options = json.loads(raw_options)
        except (TypeError, ValueError):
            try:
                raw_options = literal_eval(raw_options)
            except (ValueError, SyntaxError):
                raw_options = []
    options = [str(item).strip() for item in (raw_options or []) if str(item).strip()]
    if not options:
        return f"{question}\n\n请直接回复你的选择或补充具体需求。"
    numbered = "\n".join(f"{index}. {option}" for index, option in enumerate(options, start=1))
    return f"{question}\n\n请直接回复序号或选项文字：\n{numbered}"


def _visible_history_for_prompt_suggestion(
    history: list,
    max_messages: int = 80,
    max_chars: int = 9000,
    max_message_chars: int = 100_000,
) -> list[dict]:
    visible: list[dict] = []
    total = 0
    for msg in reversed(history or []):
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content or msg.get("tool_calls"):
            continue
        content = re.sub(r"\s+", " ", content)[:max_message_chars]
        total += len(content)
        visible.append({"role": role, "content": content})
        if len(visible) >= max_messages or total >= max_chars:
            break
    return list(reversed(visible))


def _sanitize_prompt_suggestion(text: str, max_len: int = 220) -> str:
    suggestion = str(text or "").strip()
    if not suggestion:
        return ""
    if re.search(r"(?is)<think(?:ing)?\b", suggestion) and not re.search(r"(?is)</think(?:ing)?\s*>", suggestion):
        return ""
    suggestion = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", suggestion)
    suggestion = re.sub(r"(?is)<thinking\b[^>]*>.*?</thinking\s*>", "", suggestion)
    suggestion = re.sub(r"(?is)</?think(?:ing)?\b[^>]*>", "", suggestion)
    suggestion = re.sub(r"^```(?:\w+)?\s*", "", suggestion)
    suggestion = re.sub(r"\s*```$", "", suggestion)
    suggestion = suggestion.strip().strip("\"'“”‘’`")
    suggestion = re.sub(
        r"^(?:建议|用户下一步|下一条消息|next message|suggestion|user)\s*[:：]\s*",
        "",
        suggestion,
        flags=re.IGNORECASE,
    ).strip()
    suggestion = re.sub(r"^```(?:\w+)?\s*", "", suggestion)
    suggestion = re.sub(r"\s*```$", "", suggestion)
    suggestion = re.sub(r"^\s*[-*•]\s*", "", suggestion).strip()
    suggestion = re.sub(r"[ \t]+", " ", suggestion)
    suggestion = re.sub(r"\n{3,}", "\n\n", suggestion)
    if len(suggestion) > max_len:
        suggestion = suggestion[:max_len].rstrip("，,。.!！?？;；:：、 ")
    return suggestion


def _prompt_content_text(content) -> str:
    """Normalise OpenAI-compatible string and multipart final content."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            if key in content:
                return _prompt_content_text(content[key])
        return ""
    if isinstance(content, list):
        return "\n".join(
            part for part in (_prompt_content_text(item) for item in content) if part
        )
    return str(getattr(content, "text", "") or "")


def _final_prompt_suggestion_content(message) -> str:
    """Keep final answer, discard inline/separate provider reasoning fields."""
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    text = _prompt_content_text(content).strip()
    visible, _reasoning = split_reasoning_tags(text)
    # Other reasoning models use <analysis> / <reasoning>, including an
    # unclosed tag when output stops at the token budget.
    visible = re.sub(
        r"<(?:analysis|reasoning)\b[^>]*>.*?(?:</(?:analysis|reasoning)>|$)", "", visible,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return visible.strip()


def _prompt_suggestion_token_budget(config) -> int:
    """80% of the selected model's configured maximum completion length."""
    from LLM.llm_config_manager import auxiliary_token_limits

    _context_window, max_output_tokens = auxiliary_token_limits(config)
    return max_output_tokens


def _build_prompt_suggestion_messages(
    history: list,
    lang: str = "zh",
    *,
    max_context_tokens: int | None = None,
) -> list[dict]:
    # The history is text, so convert the model's token allowance with the
    # same conservative estimator used by the agent executor.
    max_chars = 9000 if not max_context_tokens else max(9000, int(max_context_tokens * 3.5))
    visible = _visible_history_for_prompt_suggestion(history, max_chars=max_chars)
    if len(visible) < 2:
        return []
    language_hint = (
        "Prefer Chinese unless the recent conversation is clearly in another language."
        if lang != "en" else
        "Prefer English unless the recent conversation is clearly in another language."
    )
    return [
        {"role": "system", "content": get_system_prompt()},
        *visible,
        {
            "role": "system",
            "content": f"{_PROMPT_SUGGESTION_DIRECTIVE}\n{language_hint}",
        },
    ]


def _fallback_prompt_suggestion(lang: str = "zh") -> str:
    return ""


def _build_team_context(sid: str, workspace_id: str = "", *, max_teams: int = 6, max_messages: int = 3) -> str:
    try:
        from agent.tools.workspace.teams import WorkspaceTeamStore

        store = WorkspaceTeamStore(sid, workspace_id=workspace_id)
        teams = store.list()
    except Exception as exc:
        log.debug("[teams] context unavailable sid=%s workspace=%s error=%s", sid, workspace_id, exc)
        return ""
    if not teams:
        return ""

    lines = [
        "Existing workspace analyst teams are available. Use team_status when details may be stale, "
        "send_message to queue work for members, and team_delegate for parallel bounded member turns.",
        "Delegated members have limited read-only tools for schema, data queries, knowledge search, "
        "and workspace file reading. They cannot mutate data or create nested teams.",
        "For a fresh user request, do not synthesize old member results as if they were new work. "
        "If prior team messages are from an earlier turn or may be stale, create/recreate the team "
        "or call team_delegate again for the required members before producing the final answer.",
        "Never invent a Dynamic Plan ID or describe a plan as created unless team_plan_create returned it. "
        "When the user asks to create or preview a plan without executing it, call team_plan_create before replying.",
    ]
    for team in teams[:max_teams]:
        name = str(team.get("name") or "")
        if not name:
            continue
        try:
            status = store.status(name)
        except Exception:
            status = team
        description = str(status.get("description") or "").strip()
        lines.append(
            f"- Team {name}: {len(status.get('members') or [])} members; "
            f"lead_unread={status.get('lead_unread_messages', 0)}; "
            f"description={description[:160] or 'none'}"
        )
        members = []
        for member in (status.get("members") or [])[:10]:
            last_message = str(member.get("last_message") or "").replace("\n", " ")[:90]
            members.append(
                f"{member.get('name', '')}"
                f"(role={member.get('role', '') or 'analyst'}, "
                f"status={member.get('status', 'idle')}, "
                f"unread={member.get('unread_messages', 0)}"
                f"{', last=' + last_message if last_message else ''})"
            )
        if members:
            lines.append("  Members: " + "; ".join(members))
        recent = status.get("recent_messages") or []
        for message in recent[-max_messages:]:
            body = str(message.get("message") or "").replace("\n", " ")[:140]
            if body:
                lines.append(
                    f"  Message {message.get('sender', '?')} -> {message.get('recipient', '?')}: {body}"
                )
    try:
        from agent.teams.dynamic_plans import DynamicTeamPlanStore

        dynamic_plans = DynamicTeamPlanStore(
            sid, workspace_id=workspace_id
        ).list()
        for plan in dynamic_plans[:8]:
            if plan.get("status") == "needs_review":
                task_ids = ", ".join(str(task.get("id")) for task in plan.get("tasks") or [])
                lines.append(
                    "  Dynamic Plan " + str(plan.get("id") or "")
                    + " requires revision: " + str(plan.get("review_summary") or "quality review blocked it")
                    + ". Select affected ids from [" + task_ids + "] and call team_delegate with team_name, review_plan_id and review_task_ids; downstream tasks are included automatically."
                )
            if plan.get("status") == "planned":
                lines.append(
                    "  Dynamic Plan " + str(plan.get("id") or "")
                    + " is planned but not running. When the user confirms execution, call team_delegate with team_name and plan_id only."
                )
            failed = [
                task for task in plan.get("tasks") or []
                if task.get("status") == "failed"
            ]
            if failed:
                lines.append(
                    "  Dynamic Plan "
                    f"{plan['id']} has retryable failed tasks: "
                    + ", ".join(str(task.get("id")) for task in failed)
                    + ". To retry, call team_delegate with team_name, "
                    "retry_plan_id and retry_task_ids; do not supply new prompts."
                )
    except Exception as exc:
        log.debug("[teams] dynamic plan context unavailable sid=%s error=%s", sid, exc)
    return "\n".join(lines)[:5000]


class ActivationRequestError(ValueError):
    def __init__(self, message: str, code: str = "invalid_activation") -> None:
        super().__init__(message)
        self.code = code


def _resolve_activation(sess, payload: dict):
    """Resolve untrusted request names into server-owned typed definitions."""
    skill_name = str(payload.get("skill") or "").strip()
    command_name = str(payload.get("command") or "").strip().lstrip("/").lower()
    internal_action = str(payload.get("internal_action") or "").strip().lower()
    # Fallback: if the LLM auto-loaded a skill via load_analysis_skill in a
    # previous turn (e.g. dashboard), keep it active for subsequent turns
    # so guard/nudge logic works (e.g. after ask_user user-reply).
    if not skill_name and not command_name and not internal_action:
        auto = getattr(sess, "auto_loaded_skill", "") or ""
        if auto:
            skill_name = auto

    # Compatibility window for S3: current confirmation cards still send
    # internal actions through `command`. S4 will emit `internal_action`.
    if command_name in INTERNAL_ACTIONS and not internal_action:
        internal_action, command_name = command_name, ""
    try:
        activation = ActivationContext(
            skill_name=skill_name,
            command_name=command_name,
            internal_action=internal_action,
        )
    except ValueError as exc:
        raise ActivationRequestError(
            "skill、command 和 internal_action 不能同时使用。",
            "activation_conflict",
        ) from exc

    if internal_action and internal_action not in INTERNAL_ACTIONS:
        raise ActivationRequestError("未知的内部操作。", "unknown_internal_action")

    from data.workspace import workspace_manager
    runtime = workspace_manager.get(sess.session_id)
    workspace_root = runtime.workdir if runtime else None
    skill_def = None
    command_def = None
    if skill_name:
        loader = SkillLoader(
            workspace_dir=(workspace_root / ".baa" / "skills") if workspace_root else None,
        )
        skill_def = loader.load_all().get(skill_name)
        if skill_def is None:
            from agent.workflows.skills import get_session_workflow_skill
            skill_def = get_session_workflow_skill(sess.session_id, skill_name)
        if skill_def is None:
            raise ActivationRequestError(
                f"未知技能：{skill_name}", "unknown_skill",
            )
    elif command_name:
        loader = CommandLoader(
            workspace_dir=(workspace_root / ".baa" / "commands") if workspace_root else None,
        )
        command_def = loader.load().get(command_name)
        if command_def is None:
            raise ActivationRequestError(
                f"未知斜杠命令：/{command_name}", "unknown_command",
            )
        if command_def.type is not CommandType.PROMPT:
            raise ActivationRequestError(
                f"/{command_def.name} 是 {command_def.type.value} 命令，不能提交给 Agent。",
                "command_not_agent_routable",
            )
        activation = ActivationContext(command_name=command_def.name)
    return activation, skill_def, command_def


def _resolve_data_context(sess, raw) -> dict | None:
    """Validate preview-selected remote SQL tables against active sources."""
    if not isinstance(raw, dict):
        return None
    requested = raw.get("tables")
    if not isinstance(requested, list):
        requested = [raw] if raw.get("table") else []
    requested = requested[:20]
    if not requested or not hasattr(sess, "_active_entries"):
        return None

    active = sess._active_entries()
    from data.sources.sql import SQLDataSource
    active_by_id = {entry.get("id"): (idx, entry.get("source"))
                    for idx, entry in enumerate(active, start=1)
                    if isinstance(entry.get("source"), SQLDataSource)}
    source_tables = {}
    all_names = []
    for source_id, (_, src) in active_by_id.items():
        try:
            source_tables[source_id] = src.list_catalog_tables()
            if not source_tables[source_id]:
                source_tables[source_id] = src.list_tables()
        except Exception:
            # Compatibility for lightweight/legacy SQL source implementations.
            try:
                source_tables[source_id] = src.list_tables()
            except Exception:
                source_tables[source_id] = []
        try:
            all_names.extend(source_tables[source_id])
        except Exception:
            pass
    collision = len(all_names) != len(set(all_names))

    valid_source_ids = {
        str(item.get("source_id") or "")
        for item in requested if isinstance(item, dict)
        and str(item.get("table") or "").strip()
           in source_tables.get(str(item.get("source_id") or ""), [])
    }
    cross_source = len(valid_source_ids) > 1

    resolved = []
    seen = set()
    for item in requested:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        table = str(item.get("table") or "").strip()
        if (source_id, table) in seen or source_id not in active_by_id:
            continue
        idx, src = active_by_id[source_id]
        if table not in source_tables.get(source_id, []):
            continue
        seen.add((source_id, table))
        resolved.append({
            "source_id": source_id,
            "source_name": getattr(src, "name", "未命名"),
            "table": table,
            "query_table": f"src{idx}__{table}" if (collision or cross_source) else table,
        })
    return {"tables": resolved} if resolved else None


def _apply_sql_analysis_context(sess, data_context: dict | None) -> list[dict]:
    """Persist validated SQL scope and return active SQL sources still unscoped."""
    from data.sources.sql import SQLDataSource
    selected_by_source: dict[str, list[str]] = {}
    for item in (data_context or {}).get("tables", []):
        selected_by_source.setdefault(item["source_id"], []).append(item["table"])

    missing = []
    changed = False
    for entry in sess._active_entries() if hasattr(sess, "_active_entries") else []:
        src = entry.get("source")
        if not isinstance(src, SQLDataSource):
            continue
        if entry["id"] in selected_by_source:
            src.set_analysis_tables(selected_by_source[entry["id"]])
            changed = True
        
        # === CUSTOM MODIFICATION START - 智能表选择：跳过已启用自动选择的数据源 ===
        # 如果数据源启用了智能表选择，不要求用户手动勾选
        auto_select_enabled = getattr(src, "_auto_select_enabled", False)
        
        # 兜底逻辑：如果是旧会话（没有 _auto_select_enabled 属性），尝试从配置文件读取
        if not auto_select_enabled and not hasattr(src, "_auto_select_enabled"):
            log.debug("[auto-table-select] old session detected for '%s', trying to load config",
                     getattr(src, "name", "SQL"))
            auto_select_enabled = _try_load_auto_select_config(src)
        
        if auto_select_enabled:
            log.debug("[auto-table-select] source '%s' has auto_select enabled",
                     getattr(src, "name", "SQL"))
            
            # === FIX: 智能选择已处理所有数据源，跳过默认表填充 ===
            # 智能选择会：
            # 1. 为最佳匹配的数据源设置表
            # 2. 清空其他数据源的表
            # 因此，这里不需要再填充默认表
            current_tables = src.get_analysis_tables()
            log.info("[auto-table-select] source '%s' current tables: %s (smart-select already handled)",
                    getattr(src, "name", "SQL"), current_tables or "[]")
            continue  # 跳过，智能选择已处理
        # === CUSTOM MODIFICATION END ===
        
        # 如果数据源没有启用auto_select，则需要用户手动选择表
        # 这种情况下，如果没有表，会被加入 missing 列表
        
        if not src.get_analysis_tables():
            missing.append({"source_id": entry["id"], "source_name": getattr(src, "name", "SQL 数据库")})
    if changed:
        sess._combined_schema_cache = None
        if hasattr(sess, "_invalidate_merged_source"):
            sess._invalidate_merged_source()
    return missing


def _try_load_auto_select_config(source) -> bool:
    """
    为旧会话的数据源尝试加载智能表选择配置。
    
    === CUSTOM MODIFICATION - 兼容旧会话 ===
    """
    from pathlib import Path
    import json
    
    try:
        # 读取配置文件
        config_file = Path(__file__).parent.parent / "config" / "default_datasource.json"
        if not config_file.exists():
            return False
        
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        if not config.get("enabled"):
            return False
        
        # 获取当前数据源的连接信息
        try:
            current_url = source._engine.url
            current_host = str(current_url.host or "")
            current_database = str(current_url.database or "")
        except Exception:
            return False
        
        # 检查多数据源配置
        datasources = config.get("datasources", [])
        for ds in datasources:
            if not ds.get("enabled", True):
                continue
            
            conn_str = ds.get("connection_string", "")
            # 简单匹配：检查主机和数据库名是否一致
            if current_host in conn_str and current_database in conn_str:
                # 找到匹配的配置
                auto_select = ds.get("auto_select_tables", False)
                if auto_select:
                    # 设置配置到数据源
                    source._auto_select_enabled = True
                    source._select_all_tables = ds.get("select_all_tables", False)
                    source._max_tables = ds.get("max_tables", 20)
                    source._table_mapping = ds.get("table_mapping", {})
                    source._default_tables = ds.get("default_tables", [])
                    source._config_loaded_at = __import__('time').time()  # 记录加载时间
                    
                    log.info("[auto-table-select] loaded config for old session source '%s', select_all=%s",
                            getattr(source, "name", "SQL"), source._select_all_tables)
                    
                    # 如果启用了"选择所有表"，立即设置
                    if source._select_all_tables:
                        try:
                            all_tables = source._all_table_names()
                            tables_to_select = all_tables[:source._max_tables]
                            if tables_to_select:
                                source.set_analysis_tables(tables_to_select)
                                log.info("[auto-table-select] selected all tables for old session '%s': %d tables",
                                        getattr(source, "name", "SQL"), len(tables_to_select))
                        except Exception as exc:
                            log.warning("[auto-table-select] failed to select all tables: %s", exc)
                    
                    return True
        
        return False
        
    except Exception as exc:
        log.debug("[auto-table-select] failed to load config for old session: %s", exc)
        return False


def _smart_select_tables_from_question(sess, question: str):
    """
    根据用户问题智能选择相关的表。
    
    === CUSTOM MODIFICATION - 多数据源智能路由机制 ===
    改进逻辑：
    1. 遍历所有数据源，计算每个数据源的匹配分数
    2. 为所有匹配的数据源设置表（支持跨库查询）
    """
    import re
    from data.sources.sql import SQLDataSource
    
    log.info("=" * 60)
    log.info("[SMART-SELECT] Starting smart table selection")
    log.info("[SMART-SELECT] Question: %s", question[:200])
    log.info("=" * 60)
    
    changed = False
    
    # 诊断：检查有多少个数据源
    all_entries = list(sess._active_entries()) if hasattr(sess, "_active_entries") else []
    sql_sources = [e for e in all_entries if isinstance(e.get("source"), SQLDataSource)]
    log.info("[SMART-SELECT] Total entries: %d, SQL sources: %d", len(all_entries), len(sql_sources))
    
    # === 新增：收集所有数据源的匹配结果 ===
    candidates = []
    
    for entry in all_entries:
        src = entry.get("source")
        if not isinstance(src, SQLDataSource):
            continue
        
        source_name = getattr(src, "name", "SQL")
        conn_str = getattr(src, "conn_str", "")
        
        # 诊断：输出数据源详情
        log.info("[SMART-SELECT] Checking source: '%s', conn_str: %s", source_name, conn_str[:50] if conn_str else "N/A")
        
        # === 新增：智能配置加载机制 ===
        # 策略：
        # 1. 如果从未加载过配置 → 加载
        # 2. 如果配置文件被修改（通过时间戳检测）→ 重新加载
        # 这样既避免重复加载，又能及时响应配置更新
        
        should_load = False
        
        if not hasattr(src, "_auto_select_enabled"):
            # 首次加载
            should_load = True
            log.info("[SMART-SELECT] Source '%s': first time loading config", source_name)
        elif not hasattr(src, "_config_loaded_at"):
            # 旧代码创建的数据源，补充时间戳
            should_load = True
            log.info("[SMART-SELECT] Source '%s':补充配置时间戳", source_name)
        else:
            # 检查配置文件是否被修改
            try:
                from pathlib import Path
                config_file = Path(__file__).parent.parent / "config" / "default_datasource.json"
                if config_file.exists():
                    config_mtime = config_file.stat().st_mtime
                    if config_mtime > getattr(src, "_config_loaded_at", 0):
                        should_load = True
                        log.info("[SMART-SELECT] Source '%s': config file updated, reloading", source_name)
            except Exception as exc:
                log.debug("[SMART-SELECT] Failed to check config file mtime: %s", exc)
        
        if should_load:
            _try_load_auto_select_config(src)
        # ===结束===
        
        # 检查是否启用了智能表选择
        auto_select_enabled = getattr(src, "_auto_select_enabled", False)
        table_mapping = getattr(src, "_table_mapping", {})
        default_tables = getattr(src, "_default_tables", [])
        
        log.info("[SMART-SELECT] Source '%s': auto_select=%s, table_mapping_rules=%d, default_tables=%d", 
                source_name, auto_select_enabled, len(table_mapping), len(default_tables))
        
        if not auto_select_enabled:
            log.info("[SMART-SELECT] Source '%s': auto_select DISABLED, skipping", source_name)
            continue
        
        log.info("[SMART-SELECT] Source '%s': auto_select ENABLED, processing...", source_name)
        
        # 如果没有映射配置，使用默认表（但不参与竞争）
        if not table_mapping:
            log.debug("[SMART-SELECT] Source '%s': no table_mapping, will only use defaults if no other match", source_name)
            if default_tables:
                # 记录为备选方案（分数为 0）
                candidates.append({
                    "source": src,
                    "source_name": source_name,
                    "score": 0,
                    "matched_patterns": [],
                    "matched_tables": set(default_tables),
                    "is_default": True
                })
            continue
        
        # 根据问题匹配表
        matched_tables = set()
        matched_patterns = []
        
        try:
            all_tables = src._all_table_names()
        except Exception as exc:
            log.warning("[SMART-SELECT] Failed to get tables for '%s': %s", source_name, exc)
            continue
        
        for keywords_pattern, table_patterns in table_mapping.items():
            try:
                # 使用正则表达式匹配关键词
                if re.search(keywords_pattern, question, re.IGNORECASE):
                    matched_patterns.append(keywords_pattern)
                    
                    # 处理每个表模式
                    for table_pattern in table_patterns:
                        # 如果是前缀匹配（以 _ 结尾），匹配所有该前缀的表
                        if table_pattern.endswith('_'):
                            prefix_tables = [t for t in all_tables if t.startswith(table_pattern)]
                            if prefix_tables:
                                matched_tables.update(prefix_tables)
                                log.debug("[SMART-SELECT] '%s': prefix pattern '%s' matched: %s",
                                         source_name, table_pattern, prefix_tables[:5])
                        else:
                            # 精确表名
                            matched_tables.add(table_pattern)
                    
                    log.debug("[SMART-SELECT] '%s': matched pattern '%s' -> tables: %s",
                             source_name, keywords_pattern, table_patterns)
            except re.error as exc:
                log.warning("[SMART-SELECT] '%s': invalid regex pattern '%s': %s",
                           source_name, keywords_pattern, exc)
        
        # 计算匹配分数
        if matched_patterns:
            # 分数 = 匹配到的关键词模式数量
            score = len(matched_patterns)
            candidates.append({
                "source": src,
                "source_name": source_name,
                "score": score,
                "matched_patterns": matched_patterns,
                "matched_tables": matched_tables,
                "is_default": False
            })
            log.info("[SMART-SELECT] '%s': matched %d patterns, score=%d, tables=%d",
                    source_name, len(matched_patterns), score, len(matched_tables))
    
    # === 选择最佳匹配的数据源 ===
    if not candidates:
        log.info("[SMART-SELECT] No candidates found, no tables will be set")
        return
    
    # 按分数排序，选择最高分
    candidates.sort(key=lambda x: (x["score"], len(x["matched_tables"])), reverse=True)
    
    log.info("[SMART-SELECT] Candidates ranking:")
    for idx, candidate in enumerate(candidates, 1):
        log.info("[SMART-SELECT]   %d. '%s': score=%d, patterns=%s, tables=%d, is_default=%s",
                idx, candidate["source_name"], candidate["score"], 
                candidate["matched_patterns"][:2], len(candidate["matched_tables"]),
                candidate["is_default"])
    
    # 选择最佳匹配
    best_candidate = candidates[0]
    
    # 如果最佳匹配是默认表（score=0），检查是否有其他更好的匹配
    if best_candidate["score"] == 0 and len(candidates) > 1:
        log.info("[SMART-SELECT] Best match is default tables, but other sources exist. Keeping defaults.")
    
    # === CUSTOM MODIFICATION - 支持多数据源匹配 + 默认表作为基础 ===
    # 改进逻辑：
    # 1. 默认表始终加载（作为基础表）
    # 2. 有关键词匹配时，叠加匹配的表
    # 3. 支持跨库查询
    
    # 过滤出真正有关键词匹配的数据源（score > 0）
    matched_sources = [c for c in candidates if c["score"] > 0]
    
    if not matched_sources:
        # 没有任何关键词匹配，只使用默认表
        log.info("[SMART-SELECT] No keyword matches, will use default tables for all sources")
    else:
        log.info("[SMART-SELECT] Found %d sources with keyword matches", len(matched_sources))
    
    # 为所有数据源设置表（包括未匹配的）
    for candidate in candidates:
        best_source = candidate["source"]
        best_source_name = candidate["source_name"]
        default_tables = getattr(best_source, "_default_tables", [])
        
        # 确定要设置的表：默认表 + 匹配的表
        if candidate in matched_sources:
            # 有关键词匹配：默认表 + 匹配表
            matched_tables = candidate["matched_tables"]
            tables_to_set = set(default_tables) | matched_tables  # 合并，去重
            log.info("[SMART-SELECT] Source '%s': default_tables=%s + matched_tables=%s",
                    best_source_name, default_tables, list(matched_tables)[:5])
        else:
            # 无关键词匹配：只用默认表
            tables_to_set = set(default_tables)
            if tables_to_set:
                log.info("[SMART-SELECT] Source '%s': using default_tables only: %s",
                        best_source_name, list(tables_to_set))
        
        if tables_to_set:
            try:
                # 验证表是否存在
                all_tables = best_source._all_table_names()
                valid_tables = [t for t in tables_to_set if t in all_tables]
                
                if valid_tables:
                    best_source.set_analysis_tables(valid_tables)
                    changed = True
                    
                    if candidate in matched_sources:
                        log.info("[SMART-SELECT] ✅ Set tables for '%s' (score=%d, default=%d + matched=%d) → %s",
                                best_source_name, candidate["score"], 
                                len(default_tables), len(matched_tables), valid_tables)
                    else:
                        log.info("[SMART-SELECT] ✅ Set default tables for '%s': %s (no keyword match)",
                                best_source_name, valid_tables)
                else:
                    log.warning("[SMART-SELECT] No valid tables found for '%s', requested: %s, available: %s",
                               best_source_name, list(tables_to_set), all_tables[:10])
            except Exception as exc:
                log.warning("[SMART-SELECT] Failed to set tables for '%s': %s",
                           best_source_name, exc)
        else:
            log.debug("[SMART-SELECT] No tables to set for '%s' (no default_tables configured)",
                     best_source_name)
    # === CUSTOM MODIFICATION END ===
    
    # 如果有改变，清除缓存
    # === CUSTOM MODIFICATION - 总是清除缓存，确保agent能获取最新schema ===
    # 即使表没有变化，也需要清除缓存，因为agent可能还在使用旧的schema
    sess._combined_schema_cache = None
    if hasattr(sess, "_invalidate_merged_source"):
        sess._invalidate_merged_source()
    # === CUSTOM MODIFICATION END ===
    
    log.info("[SMART-SELECT] Completed. Schema cache cleared.")
    # 输出所有有表的数据源
    for entry in all_entries:
        src = entry.get("source")
        if isinstance(src, SQLDataSource):
            tables = src.get_analysis_tables()
            if tables:
                log.info("[SMART-SELECT] Final: source='%s', tables=%s",
                        getattr(src, "name", "SQL"), tables)
    log.info("=" * 60)




def _build_agent(
    sess, *, workspace_id: str | None = None, source_snapshot=None,
    hook_engine=None, hook_context=None, user_id: str = "",
) -> BusinessAgent:
    provider = sess.model_provider or config_manager.get_default_provider()
    if not provider:
        raise ValueError("未配置任何 LLM 模型，请先在「模型设置」中添加模型。")
    from LLM.llm_config_manager import get_llm_client
    client = get_llm_client(provider)
    cfg = config_manager.get_config(provider)
    # Use cached schema when available; recompute only after data source changes
    # (cache is invalidated by add_source / remove_source / toggle_source / data_source setter).
    if source_snapshot is not None:
        combined_schema = source_snapshot.combined_schema
    elif hasattr(sess, "get_combined_schema"):
        if not getattr(sess, "_combined_schema_cache", None):
            sess._combined_schema_cache = sess.get_combined_schema()
        combined_schema = sess._combined_schema_cache
    else:
        combined_schema = None
    active_sources = (
        list(source_snapshot.sources) if source_snapshot is not None else
        ([e["source"] for e in sess._active_entries()]
         if hasattr(sess, "_active_entries") else [])
    )
    if not active_sources and sess.data_source:
        active_sources = [sess.data_source]

    # 若有数据源但 schema 仍为空，尝试实时获取一次。
    # 若获取后仍为空（SQL 数据源连接断开、文件丢失等），报错提示用户重新连接，
    # 而不是让 LLM 无声地空转后输出空回复。
    if active_sources and not combined_schema:
        if source_snapshot is None:
            try:
                combined_schema = sess.get_combined_schema()
                sess._combined_schema_cache = combined_schema
            except Exception as exc:
                log.warning("[chat] schema fetch failed  sid=%s  error=%s", sess.session_id, exc)
                combined_schema = None
        if not combined_schema:
            src_names = "、".join(getattr(s, "name", "未知数据源") for s in active_sources)
            raise ValueError(
                f"数据源「{src_names}」的连接已断开（可能由服务重启引起），"
                "请在侧边栏重新连接数据源后再试。"
            )

    # Build (or reuse cached) MergedDataSource when ≥2 sources are active.
    # This enables cross-source JOIN / UNION in the agent.
    merged_source = (
        source_snapshot.merged_source if source_snapshot is not None else
        (sess.get_merged_source() if hasattr(sess, "get_merged_source") else None)
    )

    src_names = [getattr(s, "name", "?") for s in active_sources]
    log.debug("[chat] build_agent  provider=%s  model=%s  active_sources=%s  merged=%s",
              provider, cfg.model, src_names, merged_source is not None)

    provider_defaults = config_manager.DEFAULT_CONFIGS.get(provider, {})
    supports_prompt_cache = getattr(cfg, "supports_prompt_cache", None)
    if supports_prompt_cache is None:
        supports_prompt_cache = provider_defaults.get(
            "supports_prompt_cache", False
        )
    prompt_cache_mode = getattr(cfg, "prompt_cache_mode", None)
    if not prompt_cache_mode:
        prompt_cache_mode = provider_defaults.get("prompt_cache_mode", "none")

    return BusinessAgent(
        client=client, model=cfg.model,
        data_source=(source_snapshot.primary if source_snapshot is not None else sess.data_source),
        combined_schema=combined_schema,
        all_sources=active_sources,
        merged_source=merged_source,
        enable_thinking=cfg.enable_thinking,
        thinking_budget=cfg.thinking_budget,
        chart_store=chart_store,
        session_chart_ids=list(getattr(sess, "chart_ids", [])),
        color_scheme=getattr(sess, "ppt_color_scheme", "mckinsey"),
        session_id=sess.session_id,
        workspace_id=workspace_id,
        user_id=user_id,
        job_runner=sess.job_runner,
        context_window=getattr(cfg, "context_window", None),
        max_output_tokens=getattr(cfg, "max_output_tokens", None),
        hook_engine=hook_engine,
        hook_context=hook_context,
        compaction_state=getattr(sess, "compaction_state", None),
        provider=provider,
        usage_recorder=sess.record_usage,
        mcp_discovery_recorder=sess.record_discovered_mcp_tools,
        supports_prompt_cache=supports_prompt_cache,
        prompt_cache_mode=prompt_cache_mode,
        prompt_cache_retention=(
            getattr(cfg, "prompt_cache_retention", None)
            or provider_defaults.get("prompt_cache_retention", "in_memory")
        ),
        cache_breakpoint_strategy=(
            getattr(cfg, "cache_breakpoint_strategy", None)
            or provider_defaults.get(
                "cache_breakpoint_strategy", "stable_prefix"
            )
        ),
    )


# ── Session lifecycle ──────────────────────────────────────────────────────

@bp.post("/api/session/new")
def new_session():
    owner_user_id = ""
    if bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1":
        from .auth import current_user
        auth_user = current_user()
        if auth_user:
            owner_user_id = auth_user["id"]
    sess = session_manager.create(owner_user_id=owner_user_id)
    
    # === CUSTOM MODIFICATION START - 自动连接默认数据源 ===
    try:
        _auto_connect_default_datasource(sess.session_id)
    except Exception as exc:
        log.warning("[session] auto-connect datasource failed sid=%s: %s", sess.session_id, exc)
    # === CUSTOM MODIFICATION END ===
    
    try:
        from agent.hooks.models import HookContext
        from data.hooks_store import load_engine

        load_engine().run_hooks(
            "session_start",
            HookContext(event_name="session_start", session_id=sess.session_id),
        )
    except Exception as exc:
        log.debug("[hooks] session_start skipped sid=%s error=%s", sess.session_id, exc)
    # Governance: trigger the 24h consolidation check on every new session.
    # workspace_id is not yet known here, so only user-level records are in
    # scope; workspace-level consolidation continues to fire at turn-end.
    if _maybe_schedule_memory_consolidation is not None and bool(
        (request.get_json(silent=True) or {}).get("memory_enabled", True)
    ):
        try:
            _start_user_id = owner_user_id or str(
                request.headers.get("X-BAA-User-ID") or "local-default"
            ).strip()[:200]
            _maybe_schedule_memory_consolidation(
                provider=config_manager.get_default_provider() or "",
                session_id=sess.session_id,
                user_id=_start_user_id,
                workspace_id="",
            )
        except Exception as exc:
            log.debug("[memory] session_start consolidation skipped sid=%s: %s", sess.session_id, exc)
    log.info("[session] created  sid=%s", sess.session_id)
    return jsonify({"session_id": sess.session_id})


@bp.get("/api/session/<sid>/ping")
def ping_session(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        log.debug("[session] ping  sid=%s  alive=False", sid)
        return jsonify({"alive": False}), 404
    from api.saved_sessions import _visible_msg_count
    cnt = _visible_msg_count(sess.history)
    log.debug("[session] ping  sid=%s  alive=True  msg_count=%d", sid, cnt)
    return jsonify({"alive": True, "msg_count": cnt})


@bp.get("/api/session/<sid>/load-current")
def load_current_session(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[session] load-current  sid=%s  not found", sid)
        return jsonify({"error": "session not found"}), 404
    from api.saved_sessions import _visible_msg_count
    cnt = _visible_msg_count(sess.history)
    log.info("[session] load-current  sid=%s  msg_count=%d", sid, cnt)
    return jsonify({
        "history":      sess.history,
        "total_input":  sess.total_input_tokens,
        "total_output": sess.total_output_tokens,
        "total_cached_input": getattr(sess, "total_cached_input_tokens", 0),
        "total_cache_write": getattr(sess, "total_cache_write_tokens", 0),
        "usage_breakdowns": list(getattr(sess, "usage_breakdowns", []))[-100:],
        "msg_count":    cnt,
    })


@bp.get("/api/session/<sid>/token-metrics")
def get_token_metrics(sid: str):
    """Return bounded per-call Token diagnostics without prompt contents."""
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    breakdowns = list(getattr(sess, "usage_breakdowns", []) or [])[-100:]
    actual_prompt = sum(
        int(item.get("actual_prompt_tokens") or 0)
        for item in breakdowns if isinstance(item, dict)
    )
    estimated_prompt = sum(
        int(item.get("payload_tokens_est") or 0)
        for item in breakdowns if isinstance(item, dict)
    )
    estimation_error_pct = (
        round(abs(estimated_prompt - actual_prompt) / actual_prompt * 100, 2)
        if actual_prompt else None
    )
    total_input = int(getattr(sess, "total_input_tokens", 0) or 0)
    total_cached = int(getattr(sess, "total_cached_input_tokens", 0) or 0)
    return jsonify({
        "ok": True,
        "calls_retained": len(breakdowns),
        "retention_limit": 100,
        "total_input_tokens": total_input,
        "total_output_tokens": int(getattr(sess, "total_output_tokens", 0) or 0),
        "total_cached_input_tokens": total_cached,
        "total_cache_write_tokens": int(
            getattr(sess, "total_cache_write_tokens", 0) or 0
        ),
        "cache_hit_ratio": (
            round(total_cached / total_input, 4) if total_input else 0.0
        ),
        "estimated_prompt_tokens_retained": estimated_prompt,
        "actual_prompt_tokens_retained": actual_prompt,
        "estimation_error_pct": estimation_error_pct,
        "breakdowns": breakdowns,
    })


@bp.post("/api/session/<sid>/clear")
def clear_history(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get_or_create(sid)
    old_count = len(sess.history)
    sess.clear_history()
    log.info("[session] clear  sid=%s  cleared=%d entries", sid, old_count)
    return jsonify({"ok": True})


# ── Chart serving ──────────────────────────────────────────────────────────

@bp.get("/api/chart/<chart_id>")
def serve_chart(chart_id: str):
    html = chart_store.get(chart_id)
    if not html:
        log.warning("[chart] not found  chart_id=%s", chart_id)
        return "Chart not found", 404
    return Response(html, mimetype="text/html")


# ── Stop ───────────────────────────────────────────────────────────────────

@bp.post("/api/session/<sid>/stop")
def stop_session(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if sess:
        sess.cancel_requested = True
        try:
            from agent.hooks.models import HookContext
            from data.hooks_store import load_engine

            load_engine().run_hooks(
                "stop",
                HookContext(event_name="stop", session_id=sid, message="stop requested"),
            )
        except Exception as exc:
            log.debug("[hooks] stop skipped sid=%s error=%s", sid, exc)
        log.info("[session] stop requested  sid=%s", sid)
    return jsonify({"ok": True})


# ── Prompt suggestion ───────────────────────────────────────────────────────

@bp.post("/api/session/<sid>/prompt-suggestion")
def prompt_suggestion(sid: str):
    log.debug("[prompt-suggestion] called sid=%s", sid)
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[prompt-suggestion] session not found sid=%s", sid)
        return jsonify({"ok": False, "suggestion": ""}), 404

    provider = sess.model_provider or config_manager.get_default_provider()
    cfg = config_manager.get_config(provider) if provider else None
    if not provider or cfg is None:
        log.warning("[prompt-suggestion] no provider/config sid=%s provider=%s", sid, provider)
        return jsonify({"ok": False, "suggestion": ""})

    from LLM.llm_config_manager import auxiliary_token_limits
    context_budget, output_budget = auxiliary_token_limits(cfg)
    lang = str((request.json or {}).get("lang") or "zh").lower()
    messages = _build_prompt_suggestion_messages(
        sess.history, lang=lang, max_context_tokens=context_budget,
    )
    if not messages:
        log.info("[prompt-suggestion] history too short sid=%s history_len=%d", sid, len(sess.history or []))
        return jsonify({"ok": False, "suggestion": ""})

    log.debug("[prompt-suggestion] calling LLM sid=%s provider=%s model=%s msg_count=%d",
              sid, provider, cfg.model, len(messages))
    try:
        from LLM.llm_config_manager import get_llm_client
        client = get_llm_client(provider)
        budget = _prompt_suggestion_token_budget(cfg)
        response = _call_with_retry(
            client.chat.completions.create,
            model=cfg.model,
            messages=messages,
            temperature=0.25,
            max_tokens=budget,
        )
        message = response.choices[0].message if response.choices else None
        raw = _final_prompt_suggestion_content(message) if message is not None else ""
        log.debug(
            "[prompt-suggestion] response sid=%s context_budget=%d output_budget=%d chars=%d has_final=%s",
            sid, context_budget, output_budget, len(raw), bool(raw),
        )
        suggestion = _sanitize_prompt_suggestion(raw)
        log.debug("[prompt-suggestion] sanitized sid=%s suggestion=%r", sid, suggestion[:200] if suggestion else "")
    except Exception as exc:
        log.warning("[prompt-suggestion] LLM call failed sid=%s error=%s", sid, exc)
        return jsonify({"ok": False, "suggestion": ""})

    log.info("[prompt-suggestion] result sid=%s ok=%s suggestion=%r", sid, bool(suggestion), suggestion[:100] if suggestion else "")
    return jsonify({"ok": bool(suggestion), "suggestion": suggestion})


# ── Chat SSE ───────────────────────────────────────────────────────────────

@bp.post("/api/session/<sid>/chat")
def chat_stream(sid: str):
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({
            "error": "请求正文必须是 JSON 对象",
            "code": "invalid_request_body",
        }), 400
    raw_message = d.get("message")
    if raw_message is not None and not isinstance(raw_message, str):
        return jsonify({
            "error": "消息必须是字符串",
            "code": "invalid_message_type",
        }), 400
    message = (raw_message or "").strip()
    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    sess = session_manager.get_or_create(sid)
    is_internal_feishu = bool(getattr(g, "feishu_inbound", False))
    user_id = str(
        request.headers.get("X-BAA-User-ID")
        or d.get("user_id")
        or "local-default"
    ).strip()[:200]
    # Cloud mode: use authenticated user and enforce token quota
    if bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1":
        from .auth import current_user
        from data.auth_store import check_quota
        auth_user = current_user()
        if not auth_user and not is_internal_feishu:
            return jsonify({"error": "请先登录", "needs_auth": True}), 401
        user_id = (
            str(getattr(sess, "owner_user_id", "") or "feishu-bot")
            if is_internal_feishu else auth_user["id"]
        )
        # Ownership check: refuse access when another user owns this session
        owner = getattr(sess, "owner_user_id", "")
        if owner and owner != user_id:
            log.warning("[security] session ownership mismatch sid=%s owner=%s requester=%s",
                       sid, owner, user_id)
            return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
        if not owner:
            sess.owner_user_id = user_id
        quota = check_quota(user_id)
        if quota["exceeded"]:
            return jsonify({
                "error": "今日免费额度已用尽（{} tokens）。您可以在设置中配置自己的 API Key 继续使用，"
                         "或明天再试。已用 {}/{}".format(
                             quota["limit"], quota["used"], quota["limit"]),
                "code": "quota_exceeded",
                "quota": quota,
            }), 403
    try:
        activation, active_skill, active_command = _resolve_activation(sess, d)
    except ActivationRequestError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    if activation.kind == "none":
        added_tools = sess.record_discovered_tools(message)
        if added_tools:
            log.info("[tools] discovered sid=%s tools=%s", sid, added_tools)
    sess.cancel_requested = False
    _command_usage_before = (
        sess.total_input_tokens,
        sess.total_output_tokens,
        sess.total_cached_input_tokens,
    )
    data_context = _resolve_data_context(sess, d.get("data_context"))
    
    # === CUSTOM MODIFICATION START - 智能表选择 ===
    # 在应用分析上下文前，先根据问题智能选择表
    log.info("[chat] before smart-select, user message: %s", message[:200])
    _smart_select_tables_from_question(sess, message)
    log.info("[chat] after smart-select, checking sources...")
    
    # 调试：检查每个数据源的表
    for entry in sess._active_entries() if hasattr(sess, "_active_entries") else []:
        from data.sources.sql import SQLDataSource
        src = entry.get("source")
        if isinstance(src, SQLDataSource):
            log.info("[chat] source '%s' has tables: %s",
                    getattr(src, "name", "SQL"),
                    src.get_analysis_tables() or "NONE")
    # === CUSTOM MODIFICATION END ===
    
    missing_sql_scope = _apply_sql_analysis_context(sess, data_context)
    if missing_sql_scope:
        return jsonify({
            "error": "请先在数据预览中为 SQL 数据库选择一张或多张分析表。",
            "code": "sql_table_selection_required",
            "sources": missing_sql_scope,
        }), 400
    from filehistory import FileHistoryError, for_session as file_history_for_session
    file_history = file_history_for_session(sid)
    file_history_snapshot_id = ""
    if file_history is not None:
        try:
            snapshot = file_history.begin_snapshot(message, sess.capture_rewind_state())
            file_history_snapshot_id = str(snapshot.get("id") or "")
        except FileHistoryError as exc:
            return jsonify({"error": str(exc), "code": "file_history_unavailable"}), 500
    conversation_job_id = sess.job_runner.begin_tracked(
        "conversation_analysis", label=message[:96],
    )
    conversation_job = sess.job_runner.get_status(conversation_job_id) or {}
    fixed_workspace_id = str(conversation_job.get("workspace_id") or "")
    from data.workspace import workspace_manager
    fixed_workspace_runtime = (
        workspace_manager.get_by_workspace(fixed_workspace_id)
        if fixed_workspace_id else None
    )
    try:
        source_snapshot = sess.acquire_data_source_snapshot()
    except Exception as exc:
        sess.job_runner.fail_tracked(
            conversation_job_id, f"Data source snapshot failed: {exc}",
        )
        if file_history is not None and file_history_snapshot_id:
            try:
                file_history.finalize_snapshot(file_history_snapshot_id, "failed")
            except FileHistoryError:
                log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
        return jsonify({"error": f"无法固定当前数据源：{exc}"}), 500
    sess.record_activation(activation, message, conversation_job_id)
    sess.job_runner.append_tracked_event(conversation_job_id, {
        "type": "conversation_activation",
        "job_id": conversation_job_id,
        "activation": activation.to_record(),
    })

    from api.saved_sessions import _visible_msg_count
    _turn_start = time.monotonic()
    log.info("[chat] turn start  sid=%s  activation=%s:%r  history=%d msgs  msg=%.80r",
             sid, activation.kind, activation.name or "(none)",
             _visible_msg_count(sess.history), message)

    def _sse(obj) -> str:
        from agent.events import serialize_event
        return f"data: {json.dumps(serialize_event(obj), ensure_ascii=False)}\n\n"

    def generate():
        # Yield immediately so Flask flushes response headers before any blocking
        # setup work (agent build, hook loading, schema snapshot). Without this,
        # the frontend `await fetch()` blocks until the first real event arrives,
        # keeping the typing-dots invisible for the entire setup phase.
        yield _sse({"type": "agent_activity", "message": ""})

        runner = sess.job_runner
        command_metric_recorded = False

        def _record_prompt_command_metric(outcome: str, error_code: str = "") -> None:
            nonlocal command_metric_recorded
            if command_metric_recorded or active_command is None:
                return
            command_metric_recorded = True
            sess.record_command_metric(
                command=active_command.name,
                command_type=active_command.type.value,
                outcome=outcome,
                duration_ms=int((time.monotonic() - _turn_start) * 1000),
                error_code=error_code,
                input_tokens=max(0, sess.total_input_tokens - _command_usage_before[0]),
                output_tokens=max(0, sess.total_output_tokens - _command_usage_before[1]),
                cached_input_tokens=max(
                    0,
                    sess.total_cached_input_tokens - _command_usage_before[2],
                ),
            )
        try:
            from agent.hooks.models import HookContext
            from data.hooks_store import load_engine

            hook_engine = load_engine()
            hook_context = HookContext(
                event_name="turn_start",
                session_id=sid,
                turn_id=conversation_job_id,
                workspace_id=fixed_workspace_id,
                workspace_name=(
                    fixed_workspace_runtime.to_dict().get("name", "")
                    if fixed_workspace_runtime is not None else ""
                ),
                workspace_path=(
                    fixed_workspace_runtime.to_dict().get("path", "")
                    if fixed_workspace_runtime is not None else ""
                ),
                message=message,
                model_provider=sess.model_provider or config_manager.get_default_provider() or "",
            )
        except Exception as exc:
            log.warning("[hooks] disabled for turn sid=%s error=%s", sid, exc)
            hook_engine = None
            hook_context = None

        try:
            agent = _build_agent(
                sess, workspace_id=fixed_workspace_id,
                source_snapshot=source_snapshot,
                hook_engine=hook_engine,
                hook_context=hook_context,
                user_id=user_id,
            )
        except ValueError as exc:
            log.error("[chat] build_agent failed  sid=%s  error=%s", sid, exc)
            runner.fail_tracked(conversation_job_id, str(exc))
            source_snapshot.release()
            _record_prompt_command_metric("error", "agent_build_failed")
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done"})
            return

        collected: list[str] = []
        collected_reasoning: list[str] = []
        turn_chart_ids: list[str] = []
        completed_normally = False
        tool_calls_in_turn: list[str] = []
        step_count = 0
        pending_steps: dict[str, list[dict]] = {}
        artifact_signatures: set[str] = set()
        stream_error = ""

        def _append_parent_artifact(artifact: dict) -> None:
            if not isinstance(artifact, dict) or not artifact:
                return
            signature = json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=str)
            if signature in artifact_signatures:
                return
            artifact_signatures.add(signature)
            runner.append_tracked_event(conversation_job_id, {
                "type": "artifact_created", "job_id": conversation_job_id,
                "artifact": artifact,
            })

        def _collect_downloads(content: str) -> None:
            for name, url in re.findall(r"\[([^\]]+)\]\((/api/output/[^)]+)\)", content or ""):
                _append_parent_artifact({
                    "type": "file", "name": name.replace("📥", "").strip(), "url": url,
                })

        def _append_tool_result_summary(event: dict) -> None:
            tool = str(event.get("tool") or "").strip()
            if not tool:
                return
            if event.get("artifacts"):
                return
            result_tools = {
                "query_data", "create_analysis_table", "delete_analysis_tables",
                "run_analysis", "profile_data", "clean_data",
                "export_excel", "export_report", "generate_ppt", "generate_dashboard",
                "workspace_read_file", "workspace_write_file", "workspace_edit_file",
                "workspace_delete_file", "workspace_move_file", "workspace_bash",
                "workspace_command", "task_create", "task_update", "team_create",
                "team_delete", "team_list", "team_status", "send_message", "agent_delegate",
                "workflow_create", "workflow_list", "workflow_start", "workflow_status",
            }
            if tool not in result_tools:
                return
            content = str(event.get("content") or "").strip()
            summary = str(event.get("summary") or "").strip()
            error = str(event.get("error") or "").strip()
            label = {
                "query_data": "query_data 查询结果",
                "create_analysis_table": "创建分析表结果",
                "delete_analysis_tables": "删除分析表结果",
                "run_analysis": "分析计算结果",
                "profile_data": "数据概况结果",
                "clean_data": "数据清洗结果",
                "export_excel": "Excel 导出结果",
                "export_report": "报告生成结果",
                "generate_ppt": "PPT 生成结果",
                "generate_dashboard": "看板生成结果",
            }.get(tool, f"{tool} 结果")
            if error:
                label = f"{label}（失败）"
            _append_parent_artifact({
                "type": "tool_result_summary",
                "tool": tool,
                "name": label,
                "summary": summary or content[:500],
                "ok": bool(event.get("ok", True)),
            })

        def _start_step(event: dict) -> None:
            nonlocal step_count
            step_count += 1
            tool = str(event.get("tool") or "unknown")
            step = {
                "step_id": f"step-{step_count}", "tool": tool,
                "display": event.get("display") or tool,
                "started_monotonic": time.monotonic(),
            }
            pending_steps.setdefault(tool, []).append(step)
            runner.append_tracked_event(conversation_job_id, {
                "type": "conversation_step_started", "job_id": conversation_job_id,
                "step_id": step["step_id"], "tool": tool,
                "display": step["display"], "step_number": step_count,
            })
            runner.update_tracked(
                conversation_job_id, min(95, step_count * 4),
                f"已执行 {step_count} 个步骤",
            )

        def _finish_step(tool: str, ok: bool = True, error: str = "", elapsed=None) -> None:
            queue = pending_steps.get(tool) or []
            if not queue:
                return
            step = queue.pop(0)
            duration = elapsed
            if duration is None:
                duration = time.monotonic() - step["started_monotonic"]
            runner.append_tracked_event(conversation_job_id, {
                "type": "conversation_step_finished", "job_id": conversation_job_id,
                "step_id": step["step_id"], "tool": tool,
                "display": step["display"], "step_number": int(step["step_id"].split("-")[-1]),
                "status": "succeeded" if ok else "failed",
                "elapsed_seconds": round(float(duration or 0), 3), "error": error or "",
            })

        # Every data-backed conversation exposes the same readable schema
        # snapshot without forcing the model to call get_schema each turn.
        schema_text = str(source_snapshot.combined_schema or "")
        if schema_text:
            from agent.tools.results import persist_large_tool_result
            _preview, schema_artifact, _budget = persist_large_tool_result(
                sid, "get_schema", schema_text,
                runtime=fixed_workspace_runtime, threshold=1, deduplicate=True,
            )
            if schema_artifact:
                schema_artifact["name"] = "get_schema 数据结构"
                sess.record_tool_audit({"recovery": {}, "artifacts": [schema_artifact]})
                _append_parent_artifact(schema_artifact)

        ppt_title       = d.get("ppt_title", "")
        ppt_slides      = d.get("ppt_slides") or []
        excel_tables    = d.get("excel_tables") or []
        excel_filename  = d.get("excel_filename", "")
        report_title    = d.get("report_title", "")
        report_sections = d.get("report_sections") or []
        dashboard_name    = d.get("dashboard_name", "")
        dashboard_widgets = d.get("dashboard_widgets") or []

        # Per-session temporary instruction — only injected when enabled.
        active_temp_prompt = (
            getattr(sess, "temp_prompt", "")
            if getattr(sess, "temp_prompt_enabled", False) else ""
        )
        if hook_engine and hook_context:
            for notification in hook_engine.run_hooks("user_prompt_submit", hook_context):
                yield _sse(notification.to_event())
            for notification in hook_engine.run_hooks("turn_start", hook_context):
                yield _sse(notification.to_event())
            hook_prompts = hook_engine.drain_prompt_messages()
            if hook_prompts:
                hook_prompt_text = "[Hook Prompt]\n" + "\n\n".join(hook_prompts)
                active_temp_prompt = (
                    f"{active_temp_prompt}\n\n{hook_prompt_text}"
                    if active_temp_prompt else hook_prompt_text
                )
        workspace_status = (
            {"mounted": True, **fixed_workspace_runtime.to_dict()}
            if fixed_workspace_runtime is not None else {"mounted": False}
        )
        recovery_context = sess.build_recovery_context(workspace_status)
        teams_enabled = bool(d.get("teams_enabled"))
        auto_match_skill = d.get("auto_match_skill", True)
        memory_enabled = d.get("memory_enabled", True)
        team_context = _build_team_context(sid, fixed_workspace_id) if teams_enabled else ""

        conversation_scope = runner.conversation_scope(conversation_job_id)
        conversation_scope.__enter__()
        try:
            for event in agent.run(
                message, list(sess.history), activation=activation,
                active_skill=active_skill, active_command=active_command,
                last_reasoning=getattr(sess, "last_reasoning", ""),
                last_prompt_tokens=getattr(sess, "last_prompt_tokens", 0),
                ppt_title=ppt_title, ppt_slides=ppt_slides,
                excel_tables=excel_tables, excel_filename=excel_filename,
                report_title=report_title, report_sections=report_sections,
                dashboard_name=dashboard_name, dashboard_widgets=dashboard_widgets,
                temp_prompt=active_temp_prompt,
                data_context=data_context,
                recovery_context=recovery_context,
                team_context=team_context,
                teams_enabled=teams_enabled,
                auto_match_skill=auto_match_skill,
                memory_enabled=memory_enabled,
                discovered_tools=frozenset(getattr(sess, "discovered_tools", []) or []),
                discovered_mcp_tools=list(
                    getattr(sess, "discovered_mcp_tools", []) or []
                ),
                mcp_catalog_version_seen=str(
                    getattr(sess, "mcp_catalog_version", "") or ""
                ),
                tool_result_artifacts=list(
                    getattr(sess, "recent_artifacts", []) or []
                ),
            ):
                if sess.cancel_requested:
                    log.info("[chat] cancelled by user  sid=%s", sid)
                    runner.cancel_tracked(conversation_job_id)
                    sess.cancel_requested = False
                    yield _sse({"type": "stopped"})
                    return

                etype = event.get("type")
                if etype == "tool_start":
                    _start_step(event)
                elif etype == "tool_audit":
                    sess.record_tool_audit(event)
                    _finish_step(
                        str(event.get("tool") or "unknown"), bool(event.get("ok", True)),
                        str(event.get("error") or ""), event.get("elapsed_seconds"),
                    )
                    for artifact in event.get("artifacts") or []:
                        _append_parent_artifact(artifact)
                    _append_tool_result_summary(event)
                    _collect_downloads(str(event.get("content") or ""))
                    # Recovery metadata is server-only; do not expose full SQL
                    # or future internal context fields through browser SSE.
                    event = {key: value for key, value in event.items() if key != "recovery"}
                elif etype == "tool_end":
                    _finish_step(str(event.get("tool") or "unknown"))
                elif etype == "artifact_created" and event.get("artifact"):
                    _append_parent_artifact(event["artifact"])
                elif etype == "skill_activated":
                    sess.auto_loaded_skill = event.get("name", "")
                elif etype == "error":
                    stream_error = str(event.get("message") or "Conversation failed")
                elif etype == "ask_user" and is_internal_feishu:
                    # The Web client renders a selectable card for this event.
                    # Feishu has no such card in this integration, so turn it
                    # into an ordinary replyable message instead of ending the
                    # mobile turn with an empty bot message.
                    event = {
                        "type": "text",
                        "content": format_feishu_ask_user(event),
                    }
                    etype = "text"
                elif etype == "hook_event":
                    runner.append_tracked_event(conversation_job_id, {
                        "type": "hook_event",
                        "job_id": conversation_job_id,
                        "hook_id": event.get("hook_id", ""),
                        "event": event.get("event", ""),
                        "ok": bool(event.get("ok", True)),
                        "output": str(event.get("output") or "")[:500],
                    })

                # Provider/fallback safety net. BusinessAgent normally separates
                # <think> during streaming, but never let an embedded block leak
                # into the final answer if a compatibility path returns it whole.
                if etype == "text":
                    visible_text, embedded_reasoning = split_reasoning_tags(
                        event.get("content", "")
                    )
                    if embedded_reasoning:
                        collected_reasoning.append(embedded_reasoning)
                        yield _sse({"type": "reasoning", "content": embedded_reasoning})
                    event = {**event, "content": visible_text}

                if etype == "chart_html":
                    cid = uuid.uuid4().hex
                    chart_store[cid] = event["html"]
                    register_artifact(
                        chart_store.path_for(cid),
                        artifact_type="chart", session_id=sid, artifact_id=f"chart:{cid}",
                    )
                    if not hasattr(sess, "chart_ids"):
                        sess.chart_ids = []
                    sess.chart_ids.append(cid)
                    turn_chart_ids.append(cid)
                    chart_title = str(
                        event.get("title")
                        or event.get("chart_type")
                        or f"图表 {len(turn_chart_ids)}"
                    ).strip()
                    chart_type = str(event.get("chart_type") or "").strip()
                    log.info("[chat] chart generated  sid=%s  chart_id=%s", sid, cid)
                    yield _sse({
                        "type": "chart_ref",
                        "chart_id": cid,
                        "title": chart_title,
                        "chart_type": chart_type,
                    })
                    _append_parent_artifact({
                        "type": "chart", "name": chart_title,
                        "chart_type": chart_type,
                        "url": f"/api/chart/{cid}", "chart_id": cid,
                    })
                elif etype == "chart_placeholder":
                    pass
                elif etype == "ppt_scheme":
                    sess.ppt_color_scheme = event.get("scheme", "mckinsey")
                elif etype == "usage":
                    sess.record_usage(
                        event.get("prompt_tokens", 0),
                        event.get("completion_tokens", 0),
                        breakdown=event.get("prompt_breakdown"),
                        cached_input_tokens=event.get("cached_input_tokens", 0),
                        cache_write_tokens=event.get("cache_write_tokens", 0),
                    )
                    # Cloud mode: record per-user daily token usage
                    if bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1":
                        from data.auth_store import add_usage
                        _total = (event.get("prompt_tokens", 0) or 0) + (event.get("completion_tokens", 0) or 0)
                        if _total > 0:
                            add_usage(user_id, _total)
                    cfg = config_manager.get_config(sess.model_provider)
                    enriched = {
                        **event,
                        "max_output_tokens": cfg.max_output_tokens if cfg else None,
                        "session_total_input":  sess.total_input_tokens,
                        "session_total_output": sess.total_output_tokens,
                    }
                    if not enriched.get("context_window"):
                        enriched["context_window"] = cfg.context_window if cfg else None
                    yield _sse(enriched)
                elif etype == "history_compacted":
                    compacted_history = event.get("history")
                    if isinstance(compacted_history, list):
                        sess.history = compacted_history
                    # Internal state mutation; the browser only needs the
                    # surrounding compaction activity events.
                else:
                    yield _sse(event)

                if etype == "text":
                    collected.append(event.get("content", ""))
                elif etype == "reasoning":
                    collected_reasoning.append(event.get("content", ""))
                elif etype == "tool_history":
                    msgs = event.get("messages", [])
                    sess.add_tool_messages(msgs)
                    names = [m.get("tool_calls", [{}])[0].get("function", {}).get("name", "")
                             for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
                    tool_calls_in_turn.extend(n for n in names if n)
                elif etype == "tool_start":
                    pass  # already logged by agent.py

            for tool, queue in list(pending_steps.items()):
                while queue:
                    _finish_step(tool, ok=not bool(stream_error), error=stream_error)
            completed_normally = True
            final_answer = "".join(collected).strip()
            if not final_answer:
                # Never post a title-only response to Feishu (or retain an
                # empty assistant turn) when a provider/tool produced no text.
                # Details remain in server logs; the user gets an actionable,
                # non-sensitive recovery message.
                final_answer = (
                    "本次处理未生成可发送的正文。请直接重试一次；"
                    "若仍未收到结果，请在网页对话查看任务状态后再继续。"
                )
                log.warning(
                    "[chat] empty final answer sid=%s internal_feishu=%s stream_error=%s",
                    sid, is_internal_feishu, bool(stream_error),
                )
            sess.add_user(message)
            sess.add_assistant(
                final_answer,
                reasoning="".join(collected_reasoning),
                chart_ids=turn_chart_ids,
            )
            if is_internal_feishu:
                # Web-originated turns already arrive in the browser through
                # this request's SSE.  Only mobile/desktop Feishu turns need
                # the incremental session bridge below.
                sess.record_feishu_inbound_event("user", message)
                sess.record_feishu_inbound_event("assistant", final_answer)
            if bool(getattr(sess, "feishu_bot_enabled", False)) and getattr(sess, "feishu_chat_id", ""):
                yield _sse({"type": "feishu_sync", "status": "sending"})
                try:
                    from data.feishu_bot_service import send_conversation_turn, send_text

                    if is_internal_feishu:
                        send_text(
                            "🤖 小垒 Agent\n" + final_answer,
                            receive_id=sess.feishu_chat_id,
                            receive_id_type="chat_id",
                        )
                    else:
                        send_conversation_turn(
                            chat_id=sess.feishu_chat_id,
                            user_message=message,
                            assistant_message=final_answer,
                        )
                    yield _sse({"type": "feishu_sync", "status": "sent"})
                except Exception as exc:
                    # A collaboration-side delivery failure must never turn a
                    # completed Web analysis into a failed conversation.
                    log.warning("[feishu] conversation sync failed sid=%s: %s", sid, exc)
                    yield _sse({"type": "feishu_sync", "status": "failed"})
            if hook_engine and hook_context:
                end_context = hook_context.child(
                    event_name="turn_end",
                    final_answer=final_answer,
                    elapsed_seconds=time.monotonic() - _turn_start,
                )
                for notification in hook_engine.run_hooks("turn_end", end_context):
                    yield _sse(notification.to_event())
            _collect_downloads(final_answer)
            if stream_error:
                runner.fail_tracked(conversation_job_id, stream_error)
            else:
                runner.succeed_tracked(conversation_job_id, {
                    "answer": final_answer,
                    "step_count": step_count,
                    "chart_ids": turn_chart_ids,
                    "activation": activation.to_record(),
                })
                if not sess.cancel_requested:
                    try:
                        if memory_enabled and _schedule_memory_extraction is not None:
                            _schedule_memory_extraction(
                                provider=sess.model_provider
                                or config_manager.get_default_provider()
                                or "",
                                session_id=sid,
                                user_id=user_id,
                                workspace_id=fixed_workspace_id,
                                user_message=message,
                                assistant_message=final_answer,
                                runner=runner,
                            )
                        # Governance piggybacks on turn end: the 24h lock-file
                        # gate makes this a cheap stat() on most turns.
                        if memory_enabled and _maybe_schedule_memory_consolidation is not None:
                            _maybe_schedule_memory_consolidation(
                                provider=sess.model_provider
                                or config_manager.get_default_provider()
                                or "",
                            session_id=sid,
                            user_id=user_id,
                            workspace_id=fixed_workspace_id,
                        )
                    except Exception as exc:
                        log.warning(
                            "[memory] extraction scheduling failed sid=%s: %s",
                            sid, exc,
                        )

            elapsed = time.monotonic() - _turn_start
            reply_preview = "".join(collected)[:120].replace("\n", " ")
            log.info(
                "[chat] turn done  sid=%s  elapsed=%.2fs  tools=%s  charts=%d  "
                "total_in=%d  total_out=%d  reply=%.120r",
                sid, elapsed, tool_calls_in_turn or "none",
                len(turn_chart_ids), sess.total_input_tokens, sess.total_output_tokens,
                reply_preview,
            )

        except Exception as exc:
            log.exception("[chat] unhandled agent error  sid=%s", sid)
            runner.fail_tracked(conversation_job_id, f"{type(exc).__name__}: {exc}")
            if hook_engine and hook_context:
                error_context = hook_context.child(event_name="error", error=str(exc))
                for notification in hook_engine.run_hooks("error", error_context):
                    yield _sse(notification.to_event())
            yield _sse({"type": "error", "message": f"内部错误：{exc}"})

        finally:
            conversation_scope.__exit__(None, None, None)
            current = runner.get_status(conversation_job_id)
            if current and current.get("status") not in {"succeeded", "failed", "canceled"}:
                if sess.cancel_requested:
                    runner.cancel_tracked(conversation_job_id)
                    sess.cancel_requested = False
                elif not completed_normally:
                    runner.fail_tracked(conversation_job_id, "Conversation stream ended before completion.")
            if file_history is not None and file_history_snapshot_id:
                current = runner.get_status(conversation_job_id) or {}
                try:
                    file_history.finalize_snapshot(
                        file_history_snapshot_id,
                        str(current.get("status") or "interrupted"),
                    )
                except FileHistoryError:
                    log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
            source_snapshot.release()
            current = runner.get_status(conversation_job_id) or {}
            status = str(current.get("status") or "")
            _record_prompt_command_metric(
                "success" if status == "succeeded" else "error",
                "" if status == "succeeded" else (status or "stream_incomplete"),
            )
            yield _sse({"type": "done"})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
