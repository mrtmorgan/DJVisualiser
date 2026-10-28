# Genre style reference (provisional)

Working reference for mapping each genre to a visual family and response emphasis.
Palettes are directions, not final colours. Tune by eye on real mixes before locking any entry.

| Genre | Family | Palette direction | Response emphasis |
|---|---|---|---|
| Afrobeats | Bright pressure | Sunset gold, warm pink, bright orange | Brighter, more active percussion response |
| Afro house (e.g. Black Coffee) | Earth pulse | Deep brown, ochre, low-key rust | Slow drift, subtle ripple, long releases |
| Amapiano | Earth pulse | Gold, teal, dark brown | Strong bass expansion |
| Dancehall | Bright pressure | Red-orange, hot pink | Sharper, more frequent ripple |
| Hip-hop | Dark centre | Near-black with a hot red-orange core | Grain-heavy, strong contour |
| R&B and open format | Cool drift | Indigo, violet, muted cyan | Gentle, sustained, low ripple |
| Unknown or mixed | Warm haze | Current preset | Current defaults |

## Notes

- Afro house and Afrobeats are kept separate. Afro house is calmer, with slow drift and long releases.
- Genre is assumed to be set per mix. Mixes that change genre partway would need timestamped segments.
- Mixed sets are assumed to use one look for the whole mix until a crossfade is needed.
- Reference images: the three poster screenshots and the Pinterest board stills (motion not captured).

## Implementation

- Each genre has a preset in `presets/genres/`, generated from the warm haze template with the palette,
  grain and response values above. Their values are provisional until tuned on real mixes.
- `presets/genres/genres.json` maps genre names to preset files. Unknown genres are rejected with the list of known ones.
- Queue jobs take `genre` or `preset`, not both.
