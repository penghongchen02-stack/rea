import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type PublicUser } from "../api";
import { useSession } from "../session";

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const { setUser } = useSession();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const r = await api<{ user: PublicUser }>(`/api/auth/${mode}`, { method: "POST", body: { email, password } });
      setUser(r.user);
      navigate(mode === "signup" ? "/pricing" : "/ev");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <form className="card" onSubmit={(e) => void submit(e)}>
        <h1>{mode === "login" ? "Log in" : "Create your account"}</h1>
        <label>
          Email
          <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            minLength={8}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="btn btn-primary" disabled={busy} type="submit">
          {mode === "login" ? "Log in" : "Sign up"}
        </button>
        <p className="muted small">
          {mode === "login" ? (
            <>No account? <Link to="/signup">Sign up</Link></>
          ) : (
            <>Already have one? <Link to="/login">Log in</Link></>
          )}
        </p>
      </form>
    </div>
  );
}
