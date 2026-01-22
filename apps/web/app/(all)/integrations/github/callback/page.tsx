"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { Loader2 } from "lucide-react";
// plane imports
import { API_BASE_URL } from "@plane/constants";
import { PlaneLogo } from "@plane/propel/icons";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";

interface CallbackResponse {
  error?: string;
}

function GitHubCallbackPage() {
  const [searchParams] = useSearchParams();
  const router = useAppRouter();
  const [error, setError] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(true);

  const handleSuccess = useCallback(
    (workspaceSlug: string) => {
      const redirectUrl = `/${workspaceSlug}/settings/imports?provider=github&status=success`;

      // Check if this is a popup window
      const opener = window.opener as Window | null;
      if (opener && !opener.closed) {
        // Notify parent window to refresh
        try {
          opener.postMessage(
            {
              type: "github-integration-success",
              workspaceSlug,
            },
            window.location.origin
          );
        } catch (e) {
          // If postMessage fails, parent will handle via URL params
          console.error("Failed to send message to parent window:", e);
        }

        // Redirect parent window and close popup
        try {
          opener.location.href = redirectUrl;
        } catch (e) {
          console.error("Failed to redirect parent window:", e);
        }

        // Close the popup
        window.close();

        // Fallback: if window.close() doesn't work (some browsers block it)
        setTimeout(() => {
          if (!window.closed) {
            router.push(redirectUrl);
          }
        }, 1000);
      } else {
        // Not a popup - just redirect
        router.push(redirectUrl);
      }
    },
    [router]
  );

  useEffect(() => {
    const processCallback = async () => {
      const installationId = searchParams.get("installation_id");
      const code = searchParams.get("code");
      const state = searchParams.get("state"); // workspaceSlug

      // Validate required parameters
      if (!installationId) {
        setError("Missing installation_id parameter from GitHub");
        setIsProcessing(false);
        return;
      }

      if (!state) {
        setError("Missing workspace information. Please try connecting GitHub again from the imports page.");
        setIsProcessing(false);
        return;
      }

      const workspaceSlug = state;

      try {
        // Call backend API to process the callback (POST request)
        const response = await fetch(`${API_BASE_URL}/api/integrations/github/callback/`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
          body: JSON.stringify({
            installation_id: installationId,
            code: code || undefined,
            workspace_slug: workspaceSlug,
          }),
        });

        const data = (await response.json()) as CallbackResponse;

        if (response.ok) {
          // Success - notify parent window and close popup, or redirect
          handleSuccess(workspaceSlug);
        } else {
          setError(data.error || "Failed to connect GitHub. Please try again.");
          setIsProcessing(false);
        }
      } catch (err) {
        console.error("GitHub callback error:", err);
        setError("An error occurred while connecting GitHub. Please try again.");
        setIsProcessing(false);
      }
    };

    void processCallback();
  }, [searchParams, handleSuccess]);

  const handleGoBack = () => {
    // Try to close popup first
    const opener = window.opener as Window | null;
    if (opener && !opener.closed) {
      window.close();
    } else {
      router.push("/");
    }
  };

  if (error) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-6 bg-layer-0">
        <PlaneLogo className="h-12 w-auto text-primary" />
        <div className="text-center">
          <h1 className="text-xl font-semibold text-primary">Connection Failed</h1>
          <p className="mt-2 text-sm text-secondary">{error}</p>
        </div>
        <button
          onClick={handleGoBack}
          className="mt-4 rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white hover:bg-accent-primary/90"
        >
          Close
        </button>
      </div>
    );
  }

  // isProcessing is used to track the callback processing state
  if (isProcessing) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-6 bg-layer-0">
        <PlaneLogo className="h-12 w-auto text-primary" />
        <div className="flex items-center gap-3">
          <Loader2 className="h-5 w-5 animate-spin text-accent-primary" />
          <span className="text-primary">Connecting GitHub...</span>
        </div>
        <p className="text-sm text-secondary">Please wait while we complete the setup.</p>
      </div>
    );
  }

  return null;
}

export default GitHubCallbackPage;
