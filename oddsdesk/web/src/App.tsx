import { useState } from "react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { DEMO } from "./api";
import { SessionProvider, useSession } from "./session";
import { OddsPage } from "./pages/Odds";
import { EvPage } from "./pages/Ev";
import { ArbsPage } from "./pages/Arbs";
import { CalculatorsPage } from "./pages/Calculators";
import { BetsPage } from "./pages/Bets";
import { PricingPage } from "./pages/Pricing";
import { AuthPage } from "./pages/Auth";

const NAV = [
  { to: "/odds", label: "Odds Screen", icon: "▦" },
  { to: "/ev", label: "+EV Bets", icon: "↗" },
  { to: "/arbitrage", label: "Arbitrage", icon: "⇄" },
  { to: "/calculators", label: "Calculators", icon: "∑" },
  { to: "/bets", label: "Bet Tracker", icon: "✓" },
  { to: "/pricing", label: "Plans", icon: "★" },
];

export function App() {
  return (
    <SessionProvider>
      <Shell />
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
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} onClick={() => setMenuOpen(false)}>
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
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
            <Route path="/ev" element={<EvPage />} />
            <Route path="/arbitrage" element={<ArbsPage />} />
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
