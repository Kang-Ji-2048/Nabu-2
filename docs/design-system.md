# Nabu workspace design system

Nabu is an internal delivery intelligence workspace. Its interface should make the current run, reviews, cited evidence, and agent activity easy to read without creating a sense of urgency. It uses warm neutral surfaces, restrained sage accents, clear typography, and small amounts of elevation.

## Design source and adaptation

The implementation applies the installed [UI UX Pro Max skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/56ea9cf174ed86786187daa9de0e6247cb0747e4/.claude/skills/ui-ux-pro-max/SKILL.md), pinned here to the inspected revision `56ea9cf`. The skill's design-system search for `internal analytics dashboard` suggested Minimalism & Swiss. Nabu adopts its restrained hierarchy and consistent spacing. Marketing-specific layouts from the generated result do not apply to this operational product.

A targeted skill search for `text contrast readable dashboard` in the `ux` domain returned **Color Contrast**, **Readable Font Size**, and **Contrast Readability**. The implementation applies those recommendations along with the skill's stack-agnostic accessibility, touch, and responsive guidance. This is a plain HTML, CSS, and JavaScript application; it uses no framework-specific styling assumptions, downloaded typefaces, or extra design dependencies.

## Color and typography

Colors are semantic CSS custom properties in `frontend/styles.css`.

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#f5f6f4` | Workspace background |
| `--paper` | `#ffffff` | Cards, dialogs, and controls |
| `--ink` | `#25332f` | Headings and primary text |
| `--secondary` | `#55645a` | Supporting body text |
| `--muted` | `#5e6c62` | Labels, metadata, and captions |
| `--green` | `#2f705b` | Links, focus rings, and positive status text |
| `--dark` | `#285d4f` | Primary actions and brand mark |
| `--tint` | `#eaf1ed` | Sage status surfaces |
| `--amber` | `#805718` | Caution text, paired with a written label |
| `--amber-bg` | `#f7f0e3` | Caution surfaces |
| `--blue` | `#456176` | Informational status text |
| `--blue-bg` | `#edf1f5` | Informational status surfaces |
| `--line` | `#e0e5de` | Decorative separators |
| `--control-line` | `#849487` | Input and control outlines |

The weakest primary text token is `--muted`: it measures 5.53:1 on white, 5.10:1 on the workspace background, and 4.82:1 on the sage tint. `--green` measures 5.10:1 on the sage tint, amber 5.62:1 on its surface, and blue 5.74:1 on its surface. These are static token calculations, not a claim that every rendered pixel or chart annotation has been audited.

The font stack uses the user's system sans serif. Desktop reading text is 14px with generous line height; compact controls are 12–13px, and secondary labels never drop below 12px. Page titles are 30px and metric values are 32px. Mobile reading text and form inputs are 16px. Numbers use tabular figures where alignment matters. Long identifiers and user-provided text wrap rather than forcing the page wider.

The skill's general 16px base is adapted to a compact desktop workspace; the 16px reading size is retained on mobile. Reducing font sizes is not used to solve responsive layout pressure.

## Layout and component rhythm

- Spacing follows a 4px rhythm, primarily 8, 12, 16, 24, and 32px. Panels use a 12px radius and a very light shadow.
- The permanent sidebar is 232px, narrowing to 220px on smaller desktops. Content is limited to 1600px.
- Below 1280px, review and activity panels stack; activity entries can still use two columns.
- Below 1050px, metric cards use two columns, the summary becomes vertical, and the review selector becomes a horizontal list above the document.
- Below 760px, navigation becomes a drawer, reading text grows, evidence cards stack, and controls gain touch space.
- Tables preserve readable columns inside their own horizontal scroll region. The document itself should not scroll sideways.
- Dialogs remain inside the dynamic viewport, scroll internally when necessary, and preserve the visible close control. Small phone widths use tighter outer gutters rather than tiny text.

## Interaction and accessibility

Interactive elements have visible focus rings. Desktop controls exceed the web minimum of 24×24 CSS pixels; touch layouts use at least 44px control heights. Links and review titles have padded hit areas, and primary actions have a stronger visual weight than secondary actions. Written status labels accompany status colors.

The interface retains native buttons, selects, and dialogs. Hover and pressed feedback must not shift surrounding layout. Motion is limited to brief control feedback, navigation entry, loading, and toast appearance; reduced-motion preferences disable animation and transitions.

Use `docs/interface.md` for the application's data and interaction contract. Browser checks should cover narrow phones, approximately 1000px layouts, wide desktop, keyboard navigation, dialog behavior, and long run or project names. Static token checks do not replace those checks.
