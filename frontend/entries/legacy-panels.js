import { installMcpPanel, mcp } from "../features/mcp.js";
import { installKnowledgePanel, knowledge } from "../features/knowledge.js";
import { businessCanvas } from "../features/business-canvas.js";

const baa = globalThis.BAA || {};
baa.mcp = mcp;
baa.knowledge = knowledge;
baa.businessCanvas = businessCanvas;
globalThis.BAA = baa;

installMcpPanel();
installKnowledgePanel();
