// Frontend entry point. All modules are evaluated first (each runs its
// own top-level wiring); init() then hydrates the page from the server.
import "./app-context.js";
import "./app-render.js";
import "./app-flow.js";
import "./app-views.js";
import { init } from "./app-settings.js";

init();
