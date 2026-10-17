// Clipboard reads remain denied. Writes are limited to this app's main document.
exports.createClipboardPermission =
  (getContents, getOrigin) => (contents, permission, origin, details) => {
    if (
      permission !== "clipboard-sanitized-write" ||
      !contents ||
      contents !== getContents() ||
      details?.isMainFrame !== true ||
      !getOrigin()
    )
      return false;
    try {
      return new URL(origin).origin === getOrigin();
    } catch {
      return false;
    }
  };
