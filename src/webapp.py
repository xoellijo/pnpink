# [2026-02-19] Chore: translate comments to English.
import webview  # pip install pywebview

HTML = """
<!doctype html>
<meta charset="utf-8">
<title>pywebview mínimo</title>
<style>
  body { margin:0; font-family:system-ui, sans-serif; background:#111; color:#eee; }
  .wrap { max-width:560px; margin:40px auto; padding:20px; background:#1b1b1b; border-radius:12px; }
  button { padding:10px 14px; border:0; border-radius:10px; background:#4e8cff; color:#fff; cursor:pointer; }
  input { padding:10px 12px; border-radius:10px; border:1px solid #333; background:#171717; color:#eee; width:260px; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  #out { margin-top:12px; white-space:pre-wrap; }
</style>
<div class="wrap">
  <h1>pywebview • ejemplo mínimo</h1>
  <p>Escribe tu nombre y pulsa el botón (llama a <code>Python</code> desde JavaScript).</p>
  <div class="row">
    <input id="name" placeholder="Tu nombre"/>
    <button onclick="greet()">Saludar</button>
  </div>
  <div id="out"></div>
</div>
<script>
async function greet() {
  const name = document.getElementById('name').value || 'anónimo';
  // Llama a Python: window.pywebview.api.greet(name)
  const msg = await window.pywebview.api.greet(name);
  document.getElementById('out').textContent = msg;
}
</script>
"""

class Api:
    def greet(self, name: str) -> str:
        return f"¡Hola, {name}! (mensaje generado en Python)"

def main():
    # Create the window with embedded HTML and expose the API to JS
    win = webview.create_window("pywebview mínimo", html=HTML, js_api=Api())
    # On Windows 10/11, this will use Edge WebView2 by default (lightweight and modern)
    webview.start(gui="edgechromium", debug=False, http_server=False)

if __name__ == "__main__":
    main()
