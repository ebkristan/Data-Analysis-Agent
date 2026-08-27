#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional, List, Literal
from dataclasses import dataclass, field, asdict
from infrastructure.paths import runtime_config_path

log = logging.getLogger(__name__)

MCP_CONFIG_FILE = runtime_config_path("mcp_config.json", "LLM/mcp_config.json")
CONFIG_DIR = MCP_CONFIG_FILE.parent


@dataclass
class MCPServerConfig:
    server_id: str
    label: str
    transport: Literal["stdio", "sse"]
    enabled: bool = True
    description: str = ""
    # stdio fields
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    # sse fields
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)


class MCPConfigManager:
    def __init__(self):
        self.servers: Dict[str, MCPServerConfig] = {}
        self._load()

    def _load(self):
        self.servers = {}
        if MCP_CONFIG_FILE.exists():
            try:
                with open(MCP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for sid, cfg in data.get("servers", {}).items():
                    self.servers[sid] = MCPServerConfig(**cfg)
            except Exception as e:
                log.error("[MCP] 加载配置失败: %s", e)

    def _save(self) -> bool:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {"servers": {sid: asdict(cfg) for sid, cfg in self.servers.items()}}
            with open(MCP_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            log.error("[MCP] 保存配置失败: %s", e)
            return False

    def add_server(self, config: MCPServerConfig) -> tuple[bool, str]:
        if not config.server_id or not config.server_id.strip():
            return False, "server_id 不能为空"
        if config.server_id in self.servers:
            return False, f"服务器 '{config.server_id}' 已存在"
        if config.transport not in ("stdio", "sse"):
            return False, f"不支持的 transport: {config.transport}"
        self.servers[config.server_id] = config
        if self._save():
            return True, f"服务器 '{config.server_id}' 已添加"
        del self.servers[config.server_id]
        return False, "保存配置失败"

    def update_server(self, server_id: str, **kwargs) -> tuple[bool, str]:
        if server_id not in self.servers:
            return False, f"服务器 '{server_id}' 不存在"
        old = self.servers[server_id]
        import dataclasses
        updates = {k: v for k, v in kwargs.items() if hasattr(old, k)}
        updated = dataclasses.replace(old, **updates)
        self.servers[server_id] = updated
        if self._save():
            return True, "已更新"
        self.servers[server_id] = old
        return False, "保存配置失败"

    def remove_server(self, server_id: str) -> tuple[bool, str]:
        if server_id not in self.servers:
            return False, f"服务器 '{server_id}' 不存在"
        cfg = self.servers.pop(server_id)
        if self._save():
            return True, f"服务器 '{server_id}' 已删除"
        self.servers[server_id] = cfg
        return False, "保存配置失败"

    def set_enabled(self, server_id: str, enabled: bool) -> tuple[bool, str]:
        if server_id not in self.servers:
            return False, f"服务器 '{server_id}' 不存在"
        self.servers[server_id].enabled = enabled
        if self._save():
            return True, "已更新"
        return False, "保存配置失败"

    def get_server(self, server_id: str) -> Optional[MCPServerConfig]:
        return self.servers.get(server_id)

    def list_servers(self) -> Dict[str, dict]:
        return {sid: asdict(cfg) for sid, cfg in self.servers.items()}

    def get_enabled_servers(self) -> List[MCPServerConfig]:
        return [cfg for cfg in self.servers.values() if cfg.enabled]


_mcp_config_manager: Optional[MCPConfigManager] = None


def get_mcp_config_manager() -> MCPConfigManager:
    global _mcp_config_manager
    if _mcp_config_manager is None:
        _mcp_config_manager = MCPConfigManager()
    return _mcp_config_manager
