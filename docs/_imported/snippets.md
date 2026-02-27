# 🧩 PnPInk Snippet System
### User Guide (v0.2 – Simplified Syntax)
## 1️⃣ What are Snippets
Snippets are **short, reusable text templates** that expand into longer fragments.\
They can generate **formatted text**, **SVG fragments**, or even long paths and URLs from compact codes.

In short:

> Snippets are a *mini language* inside PnPInk, letting you define rules like\
> :Tc(Text red 1 black) → \<tspan fill='red' stroke='black' ...\>Text\</tspan\>

Snippets are always processed **first**, immediately after DeckMaker reads the dataset —\
before variables \${var} or any other substitutions.

## 2️⃣ Defining a snippet
Each snippet is defined in a comment line starting with \# :, for example:

\# :Name(param1 param2=default ...) = replacement text

**General rules (same as all PnPInk syntax):**

- No space between the name and (.

- Parameters are separated by **spaces**, not commas.

- Use param=value for defaults.

- Escape any snippet with a leading \\ to avoid expansion.

### Example 1 – simple expansion
\# :Path(name) = assets/icons/\${name}.svg

:Path(star) → assets/icons/star.svg

Perfect for defining folders, URLs, or long attribute strings.

### Example 2 – with multiple parameters
\# :Join(a b) = \${a}/\${b}

:Join(cards heroes) → cards/heroes

## 3️⃣ Defaults and optional parts
If a parameter has a default, it’s used when no value is given.

\# :Box(text color=black) = \<rect stroke='\${color}'/\>\<text\>\${text}\</text\>

:Box(Title) → \<rect stroke='black'/\>\<text\>Title\</text\>

:Box(Title red) → \<rect stroke='red'/\>\<text\>Title\</text\>

## 4️⃣ Conditional text with \${var ...}
\${var ...} inserts its content **only if** that parameter exists and is non-empty.\
This is useful for optional attributes or flexible tags.

\# :Tf(text font size) = \<tspan font-family='\${font}'\${size font-size='\${size}'}\>\${text}\</tspan\>

- :Tf(Label Noto) → \<tspan font-family='Noto'\>Label\</tspan\>

- :Tf(Label Noto 12px) → \<tspan font-family='Noto' font-size='12px'\>Label\</tspan\>

## 5️⃣ Quoting, spaces, and escaping
- Parameters containing spaces must use quotes:\
  :Join("My Folder" file.svg) → My Folder/file.svg

- Use \\ to write a snippet literally without expanding:\
  \\Join(a b) → :Join(a b)

## 6️⃣ Nesting snippets
Snippets can call other snippets inside their parameters.\
The system expands them from **inside out**, with a safe recursion limit.

\# :B(text) = \<b\>\${text}\</b\>

\# :C(text color) = \<span fill='\${color}'\>\${text}\</span\>

:C(:B(Name) red) → \<span fill='red'\>\<b\>Name\</b\>\</span\>

This enables “composable” syntax — one snippet building on another.

## 7️⃣ One-parameter shorthand
When a snippet defines only one parameter, it automatically captures all inner text, even with spaces.

\# :Bold(text) = \<b\>\${text}\</b\>

:Bold(This is bold text) → \<b\>This is bold text\</b\>

## 8️⃣ Rich-text snippets for SVG
In SVG, rich text is written using \<tspan\> elements.\
PnPInk lets you define shorthand snippets to simplify it — effectively a **mini markup language** for formatted text.

### Example set
\# :Tb(text) = \<tspan font-weight='bold'\>\${text}\</tspan\>

\# :Ti(text) = \<tspan font-style='italic'\>\${text}\</tspan\>

\# :Td(text) = \<tspan baseline-shift='sub' font-size='65%'\>\${text}\</tspan\>

\# :Tu(text) = \<tspan baseline-shift='super' font-size='65%'\>\${text}\</tspan\>

\# :Ts(text) = \<tspan text-decoration='underline'\>\${text}\</tspan\>

\# :Tx(text) = \<tspan text-decoration='line-through'\>\${text}\</tspan\>

\# :Tc(text fill border bordercolor) = \<tspan fill='\${fill}'\${border stroke='\${bordercolor}' stroke-width='\${border}' paint-order='stroke'}\>\${text}\</tspan\>

\# :Tf(text font size) = \<tspan font-family='\${font}'\${size font-size='\${size}'}\>\${text}\</tspan\>

\# :Te(text) = \<tspan font-family='Noto Color Emoji'\>\${text}\</tspan\>

### Examples of use
:Tb(Strong) → \<tspan font-weight='bold'\>Strong\</tspan\>

:Ti(Italic :Tb(Bold))

→ \<tspan font-style='italic'\>Italic \<tspan font-weight='bold'\>Bold\</tspan\>\</tspan\>

:Tc(Text red 0.5 black)

→ \<tspan fill='red' stroke='black' stroke-width='0.5' paint-order='stroke'\>Text\</tspan\>

Rich-text snippets can be nested, combined, or used inline with normal text.

## 9️⃣ Processing order in DeckMaker
When DeckMaker reads a dataset cell:

1.  **Snippets** are expanded first (including nested calls).

2.  Then \${var} variables are replaced.

3.  Finally, layout and rendering are applied.

Because snippets are processed first, they’re ideal for building structures —\
paths, style fragments, and \<tspan\> markup — before variable substitution.

## 🔁 Summary table
| **Concept** | **Syntax** | **Example** | **Result** |
|----|----|----|----|
| Definition | \# :Name(a b=def) = text | \# :Hello(name) = Hi \${name} | defines snippet |
| Use | :Hello(World) |  | Hi World |
| Default | param=value | \# :Box(t color=red) | uses red if omitted |
| Conditional | \${p text} | \${size font-size='\${size}'} | adds only if defined |
| Escape | \\Name() |  | prevents expansion |
| Nested | :A(:B(x)) |  | inner first |
| One param | :Bold(any text) |  | captures all |
| Quoted | :Join("My Folder" file) |  | handles spaces |

## 🧠 Design principles
- **Consistent:** follows the same spacing and escaping rules as other PnPInk syntax.

- **Readable:** snippets make complex SVG text shorter and human-friendly.

- **Composable:** you can chain, nest, or extend snippets easily.

- **Deterministic:** no scripting, just safe text substitution.

In practice, snippets are a powerful way to **invent your own shorthand language** for card text, icons, or layout fragments inside DeckMaker.
