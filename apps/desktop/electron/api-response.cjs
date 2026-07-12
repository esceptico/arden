function parseApiResponseBody(contentType, text) {
  let data = null;
  if (contentType.includes("application/json") && text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  return { data, text };
}

module.exports = { parseApiResponseBody };
