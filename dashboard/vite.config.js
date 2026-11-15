import { createReadStream, stat } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const evidenceRoute = '/evidence/65f9f144df966bde_annotated_browser.mp4';
const evidencePath = resolve(dirname(fileURLToPath(import.meta.url)), '../data/processed/65f9f144df966bde_annotated_browser.mp4');

function annotatedEvidenceServer() {
  const middleware = (req, res, next) => {
    if (req.url?.split('?')[0] !== evidenceRoute) return next();
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.statusCode = 405;
      res.setHeader('Allow', 'GET, HEAD');
      res.end();
      return;
    }

    stat(evidencePath, (error, file) => {
      if (error) {
        res.statusCode = 404;
        res.end('The saved annotated video is not available in data/processed.');
        return;
      }

      const baseHeaders = {
        'Accept-Ranges': 'bytes',
        'Content-Type': 'video/mp4',
        'Content-Disposition': 'inline; filename="65f9f144df966bde_annotated_browser.mp4"',
        'Cache-Control': 'no-cache',
      };
      const range = req.headers.range;
      let start = 0;
      let end = file.size - 1;
      let status = 200;

      if (range) {
        const match = /^bytes=(\d*)-(\d*)$/.exec(range);
        if (!match || (!match[1] && !match[2])) {
          res.writeHead(416, { ...baseHeaders, 'Content-Range': `bytes */${file.size}` });
          res.end();
          return;
        }
        start = match[1] ? Number(match[1]) : Math.max(0, file.size - Number(match[2]));
        end = match[2] && match[1] ? Number(match[2]) : file.size - 1;
        end = Math.min(end, file.size - 1);
        if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start > end || start >= file.size) {
          res.writeHead(416, { ...baseHeaders, 'Content-Range': `bytes */${file.size}` });
          res.end();
          return;
        }
        status = 206;
      }

      const headers = { ...baseHeaders, 'Content-Length': end - start + 1 };
      if (status === 206) headers['Content-Range'] = `bytes ${start}-${end}/${file.size}`;
      res.writeHead(status, headers);
      if (req.method === 'HEAD') {
        res.end();
        return;
      }

      const stream = createReadStream(evidencePath, { start, end });
      stream.on('error', () => {
        if (!res.headersSent) res.statusCode = 500;
        res.end();
      });
      res.on('close', () => { if (!res.writableEnded) stream.destroy(); });
      stream.pipe(res);
    });
  };

  return {
    name: 'worksight-local-annotated-evidence',
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}

export default defineConfig({ plugins: [react(), annotatedEvidenceServer()] });
