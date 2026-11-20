import { renditionRequest } from "../web/src/renditions/api.ts";

export async function requestRendition(path = "", options = {}) {
  const response = await renditionRequest(path, options);
  return response.status === 204 ? undefined : response.json();
}
