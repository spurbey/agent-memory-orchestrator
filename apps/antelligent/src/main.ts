import { AntelligentController, mountBubble } from "./app/controller";
import "./styles/tokens.css";
import "./styles/bubble.css";
import "./styles/panel.css";
import "./styles/timeline.css";

const mode = location.hash.includes("bubble") ? "bubble" : "panel";
const root = document.querySelector<HTMLDivElement>("#app");

if (!root) throw new Error("missing #app");

if (mode === "bubble") {
  mountBubble(root);
} else {
  new AntelligentController(root).start();
}
