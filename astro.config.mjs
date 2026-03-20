import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  output: 'static',
  build: {
    format: 'directory',
  },
  // Prefetchar grannsidor vid hover (desktop) eller viewport-synlighet (mobil)
  // → navigation känns omedelbar utan att ladda alla 100 artiklar i förväg
  prefetch: {
    prefetchAll: false,
    defaultStrategy: 'hover',
  },
});
