import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

const WORKER_URL = import.meta.env.VITE_WORKER_URL as string | undefined;

const Share = () => {
  const { id } = useParams<{ id: string }>();
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isProtected, setIsProtected] = useState(false);
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    if (!id || !WORKER_URL) {
      setError(true);
      setLoading(false);
      return;
    }

    fetch(`${WORKER_URL}/api/html/${id}`)
      .then(async (res) => {
        if (!res.ok) throw new Error("Not found");

        // Check content type to determine if it's JSON (protected) or HTML
        const contentType = res.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          const data = await res.json();
          if (data.protected) {
            setIsProtected(true);
            return null;
          }
          throw new Error("Unexpected response");
        }

        // Not protected - return HTML directly
        return res.text();
      })
      .then((html) => {
        if (html !== null) {
          setContent(html);
        }
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, [id]);

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !password.trim()) return;

    setVerifying(true);
    setPasswordError("");

    try {
      const res = await fetch(`${WORKER_URL}/api/html/${id}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: password.trim() }),
      });

      if (res.status === 401) {
        setPasswordError("Incorrect password. Please try again.");
        setVerifying(false);
        return;
      }

      if (!res.ok) {
        throw new Error("Failed to verify");
      }

      const html = await res.text();
      setContent(html);
      setIsProtected(false);
    } catch {
      setPasswordError("Something went wrong. Please try again.");
    } finally {
      setVerifying(false);
    }
  };

  if (loading) {
    return (
      <div className="w-full h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">Loading shared dashboard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="flex flex-col items-center gap-6 max-w-md text-center px-4">
          <div className="w-16 h-16 rounded-full bg-[#1a1a1a] flex items-center justify-center">
            <svg
              className="w-8 h-8 text-gray-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"
              />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white mb-2">
              This shared dashboard is no longer available
            </h1>
            <p className="text-gray-400 text-sm">
              The link may be broken or the owner may have removed it.
            </p>
          </div>
          <a
            href="https://www.byaan.ai"
            className="px-4 py-2 text-sm text-blue-400 hover:text-blue-300 transition-colors"
          >
            Go to Byaan
          </a>
        </div>
      </div>
    );
  }

  // Password protection form
  if (isProtected) {
    return (
      <div className="w-full h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="flex flex-col items-center gap-6 max-w-md w-full text-center px-4">
          <div className="w-16 h-16 rounded-full bg-[#1a1a1a] flex items-center justify-center">
            <svg
              className="w-8 h-8 text-orange-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
              />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white mb-2">
              This dashboard is password protected
            </h1>
            <p className="text-gray-400 text-sm">
              Enter the password to view this shared dashboard.
            </p>
          </div>
          <form onSubmit={handlePasswordSubmit} className="w-full max-w-xs">
            <div className="space-y-3">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                className="w-full px-4 py-3 text-sm bg-[#1a1a1a] border border-[#333] rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-orange-500 transition-colors"
                autoFocus
                disabled={verifying}
              />
              {passwordError && (
                <p className="text-red-400 text-sm">{passwordError}</p>
              )}
              <button
                type="submit"
                disabled={verifying || !password.trim()}
                className="w-full px-4 py-3 text-sm bg-orange-500 hover:bg-orange-600 disabled:bg-orange-500/50 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                {verifying ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Verifying...
                  </>
                ) : (
                  "Unlock Dashboard"
                )}
              </button>
            </div>
          </form>
          <a
            href="https://www.byaan.ai"
            className="px-4 py-2 text-sm text-gray-500 hover:text-gray-400 transition-colors"
          >
            Learn more about Byaan
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-screen">
      <iframe
        srcDoc={content || ""}
        className="w-full h-full border-0"
        title="Shared Dashboard"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
      />
    </div>
  );
};

export default Share;
