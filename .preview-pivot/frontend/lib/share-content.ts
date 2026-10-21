type ShareBrowser = {
  share?: (data: ShareData) => Promise<void>;
  clipboard?: Pick<Clipboard, "writeText">;
};

export async function shareContent(
  data: { title: string; url: string },
  browser: ShareBrowser = navigator,
): Promise<"shared" | "copied" | "cancelled"> {
  if (typeof browser.share === "function") {
    try {
      await browser.share(data);
      return "shared";
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        return "cancelled";
      }
      throw error;
    }
  }

  if (!browser.clipboard?.writeText) {
    throw new Error("Clipboard sharing is unavailable.");
  }

  await browser.clipboard.writeText(data.url);
  return "copied";
}
