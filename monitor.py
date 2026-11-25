"""Monitor de preço RTX 5060 / 5060 Ti na Kabum.

Uso:
  python monitor.py            -> verifica uma vez e notifica se algo ficou abaixo do preço máximo
  python monitor.py --listar   -> só mostra os preços atuais (não notifica)
  python monitor.py --teste    -> envia uma notificação de teste (Windows + celular)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
CONFIG = PASTA / "config.json"
ESTADO = PASTA / "estado.json"
LOG = PASTA / "monitor.log"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BUSCAS = ["rtx-5060"]
PAGINAS = 3  # 100 itens por página; os resultados relevantes vêm primeiro


def log(msg):
    linha = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(linha)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def modelo_da_placa(nome):
    if not re.search(r"placa\s+de\s+v[ií]deo", nome, re.I):
        return None
    if re.search(r"rtx\s*5060\s*ti\b", nome, re.I):
        return "RTX 5060 Ti"
    if re.search(r"rtx\s*5060\b", nome, re.I):
        return "RTX 5060"
    return None


def preco_efetivo(item):
    """Menor preço à vista válido agora (oferta relâmpago > PIX > normal)."""
    agora = time.time()
    oferta = item.get("offer") or {}
    if oferta and oferta.get("startsAt", 0) <= agora <= oferta.get("endsAt", 0) \
            and (oferta.get("quantityAvailable") or 0) > 0:
        p = oferta.get("priceWithDiscount") or oferta.get("price")
        if p:
            return float(p), "oferta"
    p = item.get("priceWithDiscount") or item.get("price")
    return (float(p), "pix") if p else (None, None)


def buscar_kabum(incluir_marketplace=True):
    produtos = {}
    for termo in BUSCAS:
        for pag in range(1, PAGINAS + 1):
            url = f"https://www.kabum.com.br/busca/{termo}?page_number={pag}&page_size=100"
            try:
                html = baixar(url)
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
                itens = json.loads(m.group(1))["props"]["pageProps"]["data"]["catalogServer"]["data"]
            except Exception as e:
                log(f"ERRO ao ler {url}: {e}")
                continue
            for it in itens:
                modelo = modelo_da_placa(it.get("name", ""))
                if not modelo or not it.get("available"):
                    continue
                if not incluir_marketplace and (it.get("flags") or {}).get("isMarketplace"):
                    continue
                preco, tipo = preco_efetivo(it)
                if not preco:
                    continue
                produtos[it["code"]] = {
                    "modelo": modelo,
                    "nome": it["name"],
                    "preco": preco,
                    "tipo": tipo,
                    "vendedor": it.get("sellerName", ""),
                    "url": f"https://www.kabum.com.br/produto/{it['code']}/{it.get('friendlyName', '')}",
                }
    return list(produtos.values())


def notificar_windows(titulo, msg, url):
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                      .replace('"', "&quot;").replace("'", "&apos;"))
    xml = (f'<toast activationType="protocol" launch="{esc(url)}" duration="long">'
           f'<visual><binding template="ToastGeneric"><text>{esc(titulo)}</text><text>{esc(msg)}</text>'
           f'</binding></visual><actions><action content="Abrir página" activationType="protocol" '
           f'arguments="{esc(url)}"/></actions></toast>')
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:TOAST_XML);"
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($t)"
    )
    env = dict(os.environ, TOAST_XML=xml)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   env=env, capture_output=True, creationflags=0x08000000)


def notificar_celular(topico, titulo, msg, url):
    if not topico or topico.startswith("TROQUE"):
        return
    dados = json.dumps({"topic": topico, "title": titulo, "message": msg, "click": url,
                        "tags": ["moneybag"], "priority": 4,
                        "actions": [{"action": "view", "label": "Abrir página", "url": url}]}).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=dados,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        log(f"ERRO ao enviar para o celular: {e}")


def notificar(cfg, titulo, msg, url):
    if cfg.get("notificar_windows", True):
        notificar_windows(titulo, msg, url)
    notificar_celular(cfg.get("ntfy_topico"), titulo, msg, url)


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    # Na nuvem (GitHub Actions) o tópico vem de um "secret" e não há notificação do Windows
    if os.environ.get("NTFY_TOPICO"):
        cfg["ntfy_topico"] = os.environ["NTFY_TOPICO"]
    if os.name != "nt":
        cfg["notificar_windows"] = False

    if "--teste" in sys.argv:
        notificar(cfg, "Teste do monitor RTX", "Se você está vendo isto, as notificações funcionam!",
                  "https://www.kabum.com.br/busca/rtx-5060")
        print("Notificação de teste enviada.")
        return

    produtos = buscar_kabum(cfg.get("incluir_marketplace", True))
    if not produtos:
        log("Nenhum produto encontrado (a página da Kabum pode ter mudado ou bloqueou o acesso).")
        sys.exit(1)

    if "--listar" in sys.argv:
        for modelo in cfg["modelos"]:
            lista = sorted((p for p in produtos if p["modelo"] == modelo), key=lambda p: p["preco"])
            print(f"\n=== {modelo} (limite {brl(cfg['modelos'][modelo]['preco_max'])}) — {len(lista)} ofertas ===")
            for p in lista[:10]:
                print(f"  {brl(p['preco']):>13} ({p['tipo']})  {p['nome'][:70]}\n                 {p['url']}")
        return

    estado = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    margem = float(cfg.get("renotificar_se_cair_reais", 20))
    novos = 0
    for p in produtos:
        limite = cfg["modelos"].get(p["modelo"], {}).get("preco_max")
        if limite is None or p["preco"] > limite:
            continue
        ja = estado.get(p["url"])
        if ja is not None and p["preco"] > ja - margem:
            continue  # já avisado e não caiu o suficiente desde então
        titulo = f"{p['modelo']} por {brl(p['preco'])}!"
        msg = f"{p['nome'][:90]}\nKabum ({p['vendedor']}) — limite {brl(limite)}"
        notificar(cfg, titulo, msg, p["url"])
        log(f"ALERTA: {titulo} {p['url']}")
        estado[p["url"]] = p["preco"]
        novos += 1

    ESTADO.write_text(json.dumps(estado, indent=1), encoding="utf-8")
    menores = {m: min((p["preco"] for p in produtos if p["modelo"] == m), default=None) for m in cfg["modelos"]}
    log(f"Verificado: {len(produtos)} ofertas; menores: "
        + ", ".join(f"{m} {brl(v) if v else '-'}" for m, v in menores.items())
        + f"; alertas novos: {novos}")


if __name__ == "__main__":
    main()
