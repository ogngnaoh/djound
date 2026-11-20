import { createReadStream, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

const path = (relative: string) =>
  fileURLToPath(new URL(relative, import.meta.url));
const recordings = new Map([
  ["/media/original.wav", path("../fixtures/audio/local-demo-source.wav")],
  ["/media/edit.wav", path("../fixtures/audio/evening-study-jazz.wav")],
]);

// The listening room uses the same project recordings in development and in the JAR.
function exampleAudio(): Plugin {
  return {
    name: "djound-example-audio",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const audio = recordings.get((req.url ?? "").split("?")[0]);
        if (!audio) return next();
        res.setHeader("Content-Type", "audio/wav");
        const stream = createReadStream(audio);
        stream.on("error", next);
        stream.pipe(res);
      });
    },
    generateBundle() {
      for (const notice of [
        "fonts/instrument-sans-OFL.txt",
        "fonts/source-sans-3-OFL.txt",
        "vendor/THREE-LICENSE.txt",
      ]) {
        this.emitFile({
          type: "asset",
          fileName: "licenses/" + notice.split("/")[1],
          source: readFileSync(path("./app/" + notice)),
        });
      }
      for (const [route, audio] of recordings) {
        this.emitFile({
          type: "asset",
          fileName: route.slice(1),
          source: readFileSync(audio),
        });
      }
    },
  };
}

export default defineConfig({
  root: path("./app"),
  publicDir: false,
  plugins: [exampleAudio()],
  build: {
    outDir: path("./dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: [
        path("./app/index.html"),
        path("./app/library.html"),
        path("./app/studio.html"),
      ],
    },
  },
  server: {
    host: "127.0.0.1",
    fs: {
      allow: [
        path("./app"),
        path("./src"),
        path("./node_modules"),
        path("../server/src/main/resources/demo"),
      ],
    },
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  preview: { host: "127.0.0.1" },
});
