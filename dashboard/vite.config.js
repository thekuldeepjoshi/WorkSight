import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // React handles the UI while local API calls are forwarded to Flask.
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:5001' } },
});
