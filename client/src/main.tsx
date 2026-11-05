import React, { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { PostHogProvider, usePostHog } from "posthog-js/react";
import posthog from "posthog-js";
import { queryClient } from "./lib/queryClient";
import { getPostHogConfig } from "./lib/config";
import { isAnalyticsOptedOut } from "./lib/analyticsPreference";
import "./index.css";
import App from "./App.tsx";
import BackendGate from "./components/BackendGate";
import { CommandPaletteProvider } from "./contexts/CommandPaletteContext";

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    // Capture the error in PostHog
    if (posthog) {
      posthog.captureException(error, {
        extra: {
          componentStack: errorInfo.componentStack,
          source: "react_error_boundary",
        },
      });
    }
    console.error("React Error Boundary caught an error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
            height: "100vh",
            fontFamily: "system-ui, sans-serif",
            padding: "20px",
            textAlign: "center",
          }}
        >
          <h1 style={{ color: "#e53e3e", marginBottom: "16px" }}>
            Something went wrong
          </h1>
          <p style={{ color: "#666", marginBottom: "24px" }}>
            The application encountered an error. Please refresh the page to continue.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: "10px 20px",
              backgroundColor: "#3182ce",
              color: "white",
              border: "none",
              borderRadius: "6px",
              cursor: "pointer",
              fontSize: "14px",
            }}
          >
            Refresh Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

function PostHogFlushHandler({ children }: { children: React.ReactNode }) {
  const posthog = usePostHog();

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (posthog) {
        console.log("Flushing PostHog events before window close");
        posthog.capture("app_close");
        // PostHog's shutdown method flushes pending events
        posthog.shutdown();
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [posthog]);

  return <>{children}</>;
}

function AppWithConfig() {
  const [config, setConfig] = useState<{
    apiKey: string | null;
    host: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getPostHogConfig().then((posthogConfig) => {
      setConfig({
        apiKey: posthogConfig.api_key,
        host: posthogConfig.host,
      });
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
          fontFamily: "system-ui, sans-serif",
          color: "#666",
        }}
      >
        Loading...
      </div>
    );
  }

  const options = {
    api_host: config?.host || "https://us.i.posthog.com",
    autocapture: true, // Explicitly enable autocapture for Tauri
    persistence: "localStorage", // Use localStorage for persistence in desktop app
    capture_exceptions: {
      capture_unhandled_errors: true,
      capture_unhandled_rejections: true,
      capture_console_errors: false,
    },
    before_send: (event: any) => {
      if (event?.event === "$exception") {
        const values = (event.properties?.$exception_list ?? [])
          .map((e: any) => String(e?.value ?? ""))
          .join(" ");
        // Drop network noise: backend still booting or machine offline
        if (/Load failed|Failed to fetch|NetworkError|Could not connect|status: 404/i.test(values)) {
          return null;
        }
      }
      return event;
    },
    loaded: (posthog: any) => {
      console.log("PostHog loaded successfully", {
        api_host: posthog.config.api_host,
        persistence: posthog.config.persistence,
      });
      if (isAnalyticsOptedOut()) {
        posthog.opt_out_capturing();
      }
    },
  };

  if (!config?.apiKey) {
    console.warn("PostHog API key not found - analytics disabled");
    return (
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <CommandPaletteProvider>
              <BackendGate>
                <App />
              </BackendGate>
            </CommandPaletteProvider>
          </BrowserRouter>
          <ReactQueryDevtools initialIsOpen={false} />
        </QueryClientProvider>
      </ErrorBoundary>
    );
  }

  return (
    <PostHogProvider apiKey={config.apiKey} options={options}>
      <ErrorBoundary>
        <PostHogFlushHandler>
          <QueryClientProvider client={queryClient}>
            <BrowserRouter>
              <CommandPaletteProvider>
                <BackendGate>
                  <App />
                </BackendGate>
              </CommandPaletteProvider>
            </BrowserRouter>
            <ReactQueryDevtools initialIsOpen={false} />
          </QueryClientProvider>
        </PostHogFlushHandler>
      </ErrorBoundary>
    </PostHogProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppWithConfig />
  </StrictMode>,
);
