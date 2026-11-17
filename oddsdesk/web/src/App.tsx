import { useState } from "react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { DEMO } from "./api";
import { SessionProvider, useSession } from "./session";
import { OddsPage } from "./pages/Odds";
import { EvPage } from "./pages/Ev";
import { ArbsPage, LowHoldPage } from "./pages/Arbs";
import { FuturesPage } from "./pages/Futures";
import { MiddlesPage } from "./pages/Middles";
import { MovesPage } from "./pages/Moves";
import { PropsPage } from "./pages/Props";
import { PrefsProvider } from "./prefs";
import { CalculatorsPage } from "./pages/Calculators";
import { BetsPage } from "./pages/Bets";
import { PricingPage } from "./pages/Pricing";
import { AuthPage } from "./pages/Auth";

const NAV = [
  {
    section: "Odds",
    items: [
      { to: "/odds", label: "Compare Odds", icon: "▦" },
      { to: "/props", label: "Player Props", icon: "◉" },
      { to: "/futures", label: "Futures", icon: "⌛" },
    ],
  },
  {
    section: "Edges",
    items: [
      { to: "/ev", label: "+EV Bets", icon: "↗" },
      { to: "/sharp-ev", label: "Sharp +EV", icon: "◆" },
      { to: "/arbitrage", label: "Arbitrage", icon: "⇄" },
      { to: "/low-hold", label: "Low Hold", icon: "≈" },
      { to: "/middles", label: "Middles", icon: "⊟" },
      { to: "/moves", label: "Moving Lines", icon: "⤳" },
    ],
  },
  {
    section: "Tools",
    items: [
      { to: "/calculators", label: "Calculators", icon: "∑" },
      { to: "/bets", label: "Bet Tracker", icon: "✓" },
      { to: "/pricing", label: "Plans", icon: "★" },
    ],
  },
];

export function App() {
  return (
    <SessionProvider>
      <PrefsProvider>
        <Shell />
      </PrefsProvider>
    </SessionProvider>
  );
}

function Shell() {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <Link to="/odds" className="brand" onClick={() => setMenuOpen(false)}>
          <span className="brand-mark">◢</span> LineScout
        </Link>
        <nav>
          {NAV.map((group) => (
            <div key={group.section} className="nav-group">
              <div className="nav-section">{group.section}</div>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} onClick={() => setMenuOpen(false)}>
                  <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <p className="sidebar-note">
          All odds, teams and sportsbooks on this site are simulated sample data.
          {DEMO && " This demo runs entirely in your browser: accounts and bets are saved on this device only, and billing is a demo switch."}
        </p>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="menu-btn" aria-label="Menu" onClick={() => setMenuOpen((o) => !o)}>
            ☰
          </button>
          <span className="badge badge-warn">Sample data</span>
          <div className="spacer" />
          <Account />
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/odds" replace />} />
            <Route path="/odds" element={<OddsPage />} />
            <Route path="/props" element={<PropsPage />} />
            <Route path="/futures" element={<FuturesPage />} />
            <Route path="/ev" element={<EvPage key="market" method="market" />} />
            <Route path="/sharp-ev" element={<EvPage key="sharp" method="sharp" />} />
            <Route path="/arbitrage" element={<ArbsPage />} />
            <Route path="/low-hold" element={<LowHoldPage />} />
            <Route path="/middles" element={<MiddlesPage />} />
            <Route path="/moves" element={<MovesPage />} />
            <Route path="/calculators" element={<CalculatorsPage />} />
            <Route path="/bets" element={<BetsPage />} />
            <Route path="/pricing" element={<PricingPage />} />
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/signup" element={<AuthPage mode="signup" />} />
            <Route path="*" element={<Navigate to="/odds" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function Account() {
  const { user, ready, logout } = useSession();
  if (!ready) return null;
  if (!user) {
    return (
      <div className="account">
        <Link to="/login" className="btn btn-ghost">Log in</Link>
        <Link to="/signup" className="btn btn-primary">Sign up</Link>
      </div>
    );
  }
  return (
    <div className="account">
      <span className={`badge ${user.plan === "pro" ? "badge-pro" : ""}`}>{user.plan === "pro" ? "PRO" : "FREE"}</span>
      <span className="muted email">{user.email}</span>
      <button className="btn btn-ghost" onClick={() => void logout()}>Log out</button>
    </div>
  );
}
