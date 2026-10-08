import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { AppProvider } from "./state";
import { Layout } from "./components/Layout";
import Chat from "./pages/Chat";
import Feed from "./pages/Feed";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Builder from "./pages/Builder";
import { RunDetail, RunsList } from "./pages/Runs";
import Approvals from "./pages/Approvals";
import Browser from "./pages/Browser";
import MemoryPage from "./pages/MemoryPage";
import Schedules from "./pages/Schedules";
import Skills from "./pages/Skills";
import SettingsPage from "./pages/SettingsPage";
import Souls from "./pages/Souls";
import Tools from "./pages/Tools";
import Tests from "./pages/Tests";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AppProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Chat />} />
            <Route path="feed" element={<Feed />} />
            <Route path="agents" element={<Agents />} />
            <Route path="agents/:id" element={<AgentDetail />} />
            <Route path="builder" element={<Builder />} />
            <Route path="runs" element={<RunsList />} />
            <Route path="runs/:id" element={<RunDetail />} />
            <Route path="approvals" element={<Approvals />} />
            <Route path="browser" element={<Browser />} />
            <Route path="memory" element={<MemoryPage />} />
            <Route path="schedules" element={<Schedules />} />
            <Route path="skills" element={<Skills />} />
            <Route path="tools" element={<Tools />} />
            <Route path="tests" element={<Tests />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="souls" element={<Souls />} />
            <Route path="*" element={<div className="p-8 text-muted">Not found</div>} />
          </Route>
        </Routes>
      </AppProvider>
    </BrowserRouter>
  </StrictMode>,
);
