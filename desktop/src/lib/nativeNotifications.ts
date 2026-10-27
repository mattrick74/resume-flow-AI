import { extensionChrome, isTauri } from "@/lib/backend";

let permissionRequested = false;

/** Native desktop notification (Tauri) or a Chrome notification (extension); browser development stays silent. */
export async function sendQueueIdleNotification(): Promise<boolean> {
  const chrome = extensionChrome();
  if (chrome?.notifications) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "/icons/icon128.png",
      title: "ResumeFlow AI queue finished",
      message: "All queued work has finished. No work remains.",
    });
    return true;
  }
  if (!isTauri()) return false;

  try {
    const notifications = await import("@tauri-apps/plugin-notification");
    let granted = await notifications.isPermissionGranted();
    if (!granted && !permissionRequested) {
      const permission = await notifications.requestPermission();
      permissionRequested = true;
      granted = permission === "granted";
    }
    if (!granted) return false;

    notifications.sendNotification({
      title: "ResumeFlow AI queue finished",
      body: "All queued work has finished. No work remains.",
    });
    return true;
  } catch (error) {
    console.warn("Could not show the queue-finished notification", error);
    return false;
  }
}
