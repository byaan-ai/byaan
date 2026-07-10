import { useState, useCallback, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useDownload } from "../hooks/useDownload";
import byaanLogo from "../assets/byaan-logo-orange.png";

const WORKER_URL = import.meta.env.VITE_WORKER_URL as string | undefined;

const NotebookLink = () => {
  const { id } = useParams<{ id: string }>();
  const { isMac, architecture, appleUrl, intelUrl, isLoading } = useDownload();
  const [showInstallPrompt, setShowInstallPrompt] = useState(false);
  const [isOpening, setIsOpening] = useState(false);
  const [checkingExistence, setCheckingExistence] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!id || !WORKER_URL) {
      setNotFound(!WORKER_URL);
      setCheckingExistence(false);
      return;
    }

    fetch(`${WORKER_URL}/api/notebook/${id}/exists`)
      .then(async (res) => {
        if (!res.ok) throw new Error("Not found");
      })
      .catch(() => setNotFound(true))
      .finally(() => setCheckingExistence(false));
  }, [id]);

  const handleOpenInByaan = useCallback(() => {
    if (!id || isOpening) return;

    setIsOpening(true);
    setShowInstallPrompt(false);

    // Create the deep link URL
    const deepLink = `byaan://import?share_id=${id}`;

    // Track if the page loses focus (app opened successfully)
    let appOpened = false;

    const handleVisibilityChange = () => {
      if (document.hidden) {
        appOpened = true;
      }
    };

    const handleBlur = () => {
      appOpened = true;
    };

    // Listen for signs that the app opened
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("blur", handleBlur);

    // Try to open the deep link
    window.location.href = deepLink;

    // After a delay, check if app opened
    setTimeout(() => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("blur", handleBlur);

      setIsOpening(false);

      // If page didn't lose focus, app probably isn't installed
      if (!appOpened && !document.hidden) {
        setShowInstallPrompt(true);
      }
    }, 2500);
  }, [id, isOpening]);

  const getDownloadUrl = () => {
    if (!isMac) return null;
    if (architecture === "apple-silicon" && appleUrl) return appleUrl;
    if (architecture === "intel" && intelUrl) return intelUrl;
    return appleUrl || intelUrl;
  };

  const downloadUrl = getDownloadUrl();

  // Loading state while checking existence
  if (checkingExistence) {
    return (
      <div className="w-full min-h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-orange-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">Checking notebook...</p>
        </div>
      </div>
    );
  }

  // Invalid link (no ID provided)
  if (!id) {
    return (
      <div className="w-full min-h-screen flex items-center justify-center bg-[#0a0a0a]">
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
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white mb-2">
              Invalid Link
            </h1>
            <p className="text-gray-400 text-sm">
              This notebook link appears to be invalid or incomplete.
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

  // Not found error (notebook doesn't exist or was deleted)
  if (notFound) {
    return (
      <div className="w-full min-h-screen flex items-center justify-center bg-[#0a0a0a]">
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
              This shared notebook is no longer available
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

  return (
    <div className="w-full min-h-screen flex items-center justify-center bg-[#0a0a0a]">
      <div className="flex flex-col items-center gap-8 max-w-md w-full text-center px-4">
        {/* Logo */}
        <img src={byaanLogo} alt="Byaan" className="w-16 h-16" />

        {/* Header */}
        <div>
          <h1 className="text-2xl font-semibold text-white mb-2">
            Open Shared Notebook
          </h1>
          <p className="text-gray-400 text-sm">
            Someone shared a Byaan notebook with you. Open it in the Byaan app
            to import and start analyzing.
          </p>
        </div>

        {/* App Not Installed Warning */}
        {showInstallPrompt && (
          <div className="w-full max-w-xs p-4 bg-orange-500/10 border border-orange-500/30 rounded-lg">
            <div className="flex items-start gap-3">
              <svg
                className="w-5 h-5 text-orange-500 flex-shrink-0 mt-0.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
              <div className="text-left">
                <p className="text-orange-400 text-sm font-medium mb-1">
                  Byaan app not detected
                </p>
                <p className="text-orange-300/70 text-xs">
                  Please install the Byaan app first, then click "Open in Byaan"
                  again.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Open in Byaan Button */}
        <button
          onClick={handleOpenInByaan}
          disabled={isOpening}
          className="w-full max-w-xs px-6 py-4 text-base bg-orange-500 hover:bg-orange-600 disabled:bg-orange-500/70 text-white font-medium rounded-lg transition-colors flex items-center justify-center gap-2"
        >
          {isOpening ? (
            <>
              <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Opening...
            </>
          ) : (
            <>
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                />
              </svg>
              Open in Byaan
            </>
          )}
        </button>

        {/* Divider */}
        <div className="w-full max-w-xs flex items-center gap-4">
          <div className="flex-1 h-px bg-gray-700" />
          <span className="text-gray-500 text-sm">
            {showInstallPrompt ? "install first" : "or"}
          </span>
          <div className="flex-1 h-px bg-gray-700" />
        </div>

        {/* Download Section */}
        <div className="w-full max-w-xs">
          <p className="text-gray-400 text-sm mb-3">
            {showInstallPrompt
              ? "Download and install Byaan to continue:"
              : "Don't have Byaan installed?"}
          </p>

          {isLoading ? (
            <div className="flex items-center justify-center py-3">
              <div className="w-5 h-5 border-2 border-gray-600 border-t-orange-500 rounded-full animate-spin" />
            </div>
          ) : isMac && downloadUrl ? (
            <a
              href={downloadUrl}
              className={`w-full inline-flex items-center justify-center px-6 py-3 text-sm rounded-lg transition-colors gap-2 ${
                showInstallPrompt
                  ? "bg-orange-500 hover:bg-orange-600 text-white"
                  : "bg-[#1a1a1a] hover:bg-[#252525] text-white border border-gray-700"
              }`}
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z" />
              </svg>
              Download for Mac
              {architecture === "apple-silicon" && " (Apple Silicon)"}
              {architecture === "intel" && " (Intel)"}
            </a>
          ) : (
            <a
              href="https://www.byaan.ai"
              className={`w-full inline-flex items-center justify-center px-6 py-3 text-sm rounded-lg transition-colors ${
                showInstallPrompt
                  ? "bg-orange-500 hover:bg-orange-600 text-white"
                  : "bg-[#1a1a1a] hover:bg-[#252525] text-white border border-gray-700"
              }`}
            >
              {showInstallPrompt ? "Get Byaan" : "Learn More About Byaan"}
            </a>
          )}
        </div>

        {/* Share ID Display */}
        <div className="w-full max-w-xs pt-4 border-t border-gray-800">
          <p className="text-gray-500 text-xs mb-1">Notebook ID</p>
          <code className="text-gray-400 text-xs font-mono break-all">
            {id}
          </code>
        </div>
      </div>
    </div>
  );
};

export default NotebookLink;
