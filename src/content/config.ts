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
    published_at: z.string().optional(),
    slug: z.string(),
    // 'ledare' = vanlig utgåva om en enskild nyhet (default, alla äldre filer)
    // 'kronika' = veckokrönika, skriven på dagar utan nyhet värd en ledare
    kind: z.enum(['ledare', 'kronika']).default('ledare'),
    // Bara för krönikor: veckans rubriker som krönikan blickar tillbaka på
    sources: z
      .array(
        z.object({
          headline: z.string(),
          url: z.string(),
          date: z.string().optional(),
        })
      )
      .optional(),
  }),
});

export const collections = { articles };
