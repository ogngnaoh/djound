// Served directly, these pages use the isolated, browser-only design preview.
// Vite defines PROD and DEV for the installed app and its development server.
export const live = import.meta.env?.MODE !== undefined;
