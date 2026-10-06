import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // Avoid native realpath resolution in the Windows sandbox. All files stay in this project.
  // This project has no linked packages; Docker and local builds use the same resolution policy.
  resolve: { preserveSymlinks: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
