import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type PublicUser } from "../api";
import { PageHeader } from "../components";
import { useSession } from "../session";

export function PricingPage() {
  const { user, setUser, meta } = useSession();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const limits = meta?.freeLimits;

  async function change(path: "checkout" | "cancel") {
    if (!user) {
      navigate("/signup");
      return;
    }
    setBusy(true);
    try {
      const r = await api<{ user: PublicUser }>(`/api/billing/${path}`, { method: "POST", body: { plan: "pro" } });
      setUser(r.user);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Plans" subtitle="Demo billing: upgrading switches your plan instantly and no payment is taken." />
      <div className="plans">
        <div className={`card plan ${user?.plan === "free" ? "current" : ""}`}>
          <h2>Free</h2>
          <div className="price">$0</div>
          <ul>
            <li>Compare odds, player props and futures</li>
            <li>All calculators and the bet tracker</li>
            <li>Top {limits?.ev ?? 3} +EV bets per search</li>
            <li>Top {limits?.arbs ?? 1} arbitrage, {limits?.middles ?? 2} middles</li>
            <li>Top {limits?.lowHold ?? 5} low-hold markets and {limits?.moves ?? 5} line moves</li>
          </ul>
          {user?.plan === "pro" ? (
            <button className="btn btn-ghost" disabled={busy} onClick={() => void change("cancel")}>Downgrade</button>
          ) : (
            <button className="btn btn-ghost" disabled>{user ? "Current plan" : "Included"}</button>
          )}
        </div>
        <div className={`card plan featured ${user?.plan === "pro" ? "current" : ""}`}>
          <h2>Pro</h2>
          <div className="price">$29<span className="muted">/mo</span></div>
          <ul>
            <li>Everything in Free</li>
            <li>Every +EV bet, by market average or sharp line</li>
            <li>Every arbitrage, middle and low-hold market</li>
            <li>Full line-movement feed</li>
          </ul>
          {user?.plan === "pro" ? (
            <button className="btn btn-primary" disabled>Current plan</button>
          ) : (
            <button className="btn btn-primary" disabled={busy} onClick={() => void change("checkout")}>
              {user ? "Upgrade (demo)" : "Sign up to upgrade"}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
