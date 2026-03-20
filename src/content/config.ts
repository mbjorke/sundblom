import { defineCollection, z } from 'astro:content';

const articles = defineCollection({
  type: 'data',
  schema: z.object({
    headline: z.string(),
    julius_text: z.string(),
    body: z.string(),
    author: z.string(),
    source_url: z.string(),
    date: z.string(),
    slug: z.string(),
  }),
});

export const collections = { articles };
