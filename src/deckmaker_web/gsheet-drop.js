function candidateUrls(value) {
  if (!value) return [];
  const decoded = String(value).replaceAll('&amp;', '&');
  const matches = decoded.match(/https?:\/\/[^\s"'<>]+/gi) || [];
  return matches.length ? matches : [decoded.trim()];
}

export function parseGoogleSheetUrl(value) {
  for (const candidate of candidateUrls(value)) {
    try {
      const url = new URL(candidate);
      if (url.hostname.toLowerCase() !== 'docs.google.com') continue;
      const match = url.pathname.match(/\/spreadsheets\/d\/([^/?#]+)/i);
      if (!match) continue;
      const hash = new URLSearchParams(url.hash.replace(/^#/, ''));
      return {
        sheetId: decodeURIComponent(match[1]),
        gid: url.searchParams.get('gid') || hash.get('gid') || '',
        url: url.href,
      };
    } catch {
      // Ignore non-URL clipboard formats and keep looking.
    }
  }
  return null;
}

export function googleSheetFromDataTransfer(dataTransfer) {
  if (!dataTransfer) return null;
  let fallback = null;
  for (const type of ['text/uri-list', 'text/plain', 'text/html']) {
    const result = parseGoogleSheetUrl(dataTransfer.getData(type));
    if (result?.gid) return result;
    fallback ||= result;
  }
  return fallback;
}
