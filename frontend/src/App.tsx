import { useState } from "react";
import "./App.css";
import { ChatScreen } from "./pages/ChatScreen";
import { Dashboard } from "./pages/Dashboard";
import { IngestScreen } from "./pages/IngestScreen";
import { ManageScreen } from "./pages/ManageScreen";

type Tab = "dashboard" | "manage" | "ingest" | "chat";

function App() {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <div className="app">
      <header className="app-header">
        <h1>Agent Bhav</h1>
        <nav className="tabs">
          <button className={tab === "dashboard" ? "active" : ""} onClick={() => setTab("dashboard")}>
            Dashboard
          </button>
          <button className={tab === "manage" ? "active" : ""} onClick={() => setTab("manage")}>
            Manage
          </button>
          <button className={tab === "ingest" ? "active" : ""} onClick={() => setTab("ingest")}>
            Update Market Data
          </button>
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            Chat
          </button>
        </nav>
      </header>

      <main>
        {tab === "dashboard" && <Dashboard />}
        {tab === "manage" && <ManageScreen />}
        {tab === "ingest" && <IngestScreen />}
        {tab === "chat" && <ChatScreen />}
      </main>

      <footer className="disclaimer">
        This is informational, evidence-based flow analysis — not investment advice.
        The agent informs; you decide.
      </footer>
    </div>
  );
}

export default App;
