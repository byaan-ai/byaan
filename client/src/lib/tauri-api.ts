// Tauri API wrapper for detecting if running in Tauri and getting backend port

declare global {
  interface Window {
    __TAURI__?: any;
    __TAURI_INTERNALS__?: unknown;
  }
}

export const isTauriApp = (): boolean => {
  return (
    typeof window !== "undefined" && typeof window.__TAURI__ !== "undefined"
  );
};

export const getBackendPort = async (): Promise<number> => {
  if (!isTauriApp()) {
    return 8000;
  }

  try {
    const invokeFn: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> = window.__TAURI__?.core?.invoke
      ? window.__TAURI__.core.invoke
      : (await import("@tauri-apps/api/core")).invoke;

    const port = await invokeFn<number>("get_backend_port");
    return typeof port === "number" ? port : 8000;
  } catch (error) {
    console.error("Failed to get backend port from Tauri:", error);
    return 8000;
  }
};

export const getBackendUrl = async (): Promise<string> => {
  const port = await getBackendPort();
  const host = isTauriApp() ? '127.0.0.1' : 'localhost'
  return `http://${host}:${port}`;
};

export const toggleDevTools = async (): Promise<void> => {
  if (!isTauriApp()) {
    console.warn("DevTools toggle only works in Tauri app");
    return;
  }

  try {
    const invokeFn = window.__TAURI__?.core?.invoke
      ? window.__TAURI__.core.invoke
      : (await import("@tauri-apps/api/core")).invoke;

    await invokeFn("toggle_devtools");
  } catch (error) {
    console.error("Failed to toggle DevTools:", error);
  }
};

/**
 * Saves a Blob to disk using Tauri's file system
 * @param blob - The Blob to save
 * @param fileName - The name of the file
 * @param useDialog - Whether to use save dialog (default: false)
 * @returns The full path where the file was saved
 */
export const saveBlobToFile = async (
  blob: Blob,
  fileName: string,
  useDialog = false
): Promise<string> => {
  // Check if running in Tauri BEFORE attempting any imports
  if (!isTauriApp()) {
    throw new Error("saveBlobToFile only works in Tauri app");
  }

  try {
    // Convert Blob to Uint8Array (needed for both approaches)
    const arrayBuffer = await blob.arrayBuffer();
    const uint8Array = new Uint8Array(arrayBuffer);

    if (useDialog) {
      // Use save dialog - user chooses location
      console.log("Using save dialog for file:", fileName);
      const { save } = await import("@tauri-apps/plugin-dialog");

      // Determine file filter based on extension
      const extension = fileName.split('.').pop() || '*';
      const filterName =
        extension === 'csv' ? 'CSV Files' :
        extension === 'pdf' ? 'PDF Files' :
        extension === 'html' ? 'HTML Files' : 'All Files';

      const filePath = await save({
        defaultPath: fileName,
        filters: [{
          name: filterName,
          extensions: [extension]
        }]
      });

      // User cancelled dialog
      if (!filePath) {
        throw new Error("Save cancelled by user");
      }

      // Write file to user-selected location
      const { writeFile } = await import("@tauri-apps/plugin-fs");
      await writeFile(filePath, uint8Array);

      console.log("File saved via dialog to:", filePath);
      return filePath;
    } else {
      // Direct write to Downloads folder (faster, no user interaction)
      console.log("Attempting direct write to Downloads:", fileName);
      const { BaseDirectory, writeFile } = await import("@tauri-apps/plugin-fs");
      const { join, downloadDir } = await import("@tauri-apps/api/path");

      // Get the download directory path
      const downloadPath = await downloadDir();
      const filePath = await join(downloadPath, fileName);

      // Write the file to the download directory
      await writeFile(fileName, uint8Array, {
        baseDir: BaseDirectory.Download,
      });

      console.log("File saved directly to Downloads:", filePath);
      return filePath;
    }
  } catch (error) {
    // If permission error on direct write, retry with dialog
    if (!useDialog && error instanceof Error &&
        (error.message.includes('permission') ||
         error.message.includes('denied') ||
         error.message.includes('not allowed'))) {
      console.warn("Direct write failed, falling back to save dialog:", error.message);
      return saveBlobToFile(blob, fileName, true);
    }

    console.error("Failed to save file with Tauri:", error);
    throw error;
  }
};

/**
 * Opens a URL in the system's default browser
 * Works in both Tauri (uses shell.open) and web (uses window.open)
 * @param url - The URL to open
 */
export const openExternalUrl = async (url: string): Promise<void> => {
  if (!isTauriApp()) {
    // In browser, use window.open
    window.open(url, '_blank');
    return;
  }

  try {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
  } catch (error) {
    console.error("Failed to open URL in Tauri, falling back to window.open:", error);
    // Fallback to window.open if shell.open fails
    window.open(url, '_blank');
  }
};

/**
 * Copies text to clipboard
 * Works in both Tauri (uses clipboard plugin) and web (uses navigator.clipboard)
 * @param text - The text to copy to clipboard
 */
export const copyToClipboard = async (text: string): Promise<void> => {
  if (!isTauriApp()) {
    // In browser, use navigator.clipboard
    await navigator.clipboard.writeText(text);
    return;
  }

  try {
    const { writeText } = await import("@tauri-apps/plugin-clipboard-manager");
    await writeText(text);
  } catch (error) {
    console.error("Failed to copy with Tauri clipboard, falling back to navigator.clipboard:", error);
    // Fallback to navigator.clipboard if Tauri clipboard fails
    await navigator.clipboard.writeText(text);
  }
};

/**
 * Opens a file in Finder (macOS) or the default file manager
 * @param filePath - The full path to the file
 */
export const openFileInFinder = async (filePath: string): Promise<void> => {
  // Check if running in Tauri BEFORE attempting any imports
  if (!isTauriApp()) {
    console.warn("openFileInFinder only works in Tauri app");
    return;
  }

  try {
    // Import Tauri modules (only executes if isTauriApp() is true)
    const { Command } = await import("@tauri-apps/plugin-shell");

    // Use 'open -R' on macOS to reveal the file in Finder
    // The -R flag reveals the file in Finder instead of opening it
    await Command.create("open", ["-R", filePath]).execute();
  } catch (error) {
    console.error("Failed to open file in Finder:", error);
    throw error;
  }
};
