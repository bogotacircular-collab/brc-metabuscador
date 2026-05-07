# -*- coding: utf-8 -*-
"""
scraper.py - BRC Metabuscador
Extrae datos reales de portales colombianos de economia circular/verde
y genera data.json para el front-end estatico.
 
Fuentes:
  1. BazzarBog CCB        - HTML estatico (PrestaShop)
  2. ECMarketplace Latam  - API interna JSON
  3. Negocios Verdes CAR  - HTML estatico (SSL sin verificar)
  4. HEMA Atenea          - HTML Drupal
  5. Ecodirectorio SDA    - Google Sites (Playwright)
 
Instalar:
  pip install httpx beautifulsoup4 lxml playwright
  playwright install chromium
"""
 
import json
import re
import logging
import ssl
from datetime import datetime, timezone
from pathlib import Path
 
import httpx
from bs4 import BeautifulSoup
 
# ── CONFIG ───────────────────────────────────────────────────────────────────
OUTPUT_FILE     = Path(__file__).parent / "data.json"
LOG_FORMAT      = "%(asctime)s [%(levelname)s] %(message)s"
REQUEST_TIMEOUT = 25
TODAY           = datetime.now(timezone.utc).isoformat()
 
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("brc-scraper")
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BRC-Metabuscador/2.0; "
        "+https://bogotacircular-collab.github.io/brc-metabuscador)"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}
 
# ── CLASIFICADOR ─────────────────────────────────────────────────────────────
 
TIPO_KW = {
    "financiacion": [
        "fondo", "credito", "financiacion", "convocatoria", "beca",
        "inversion", "capital", "linea verde", "prestamo"
    ],
    "tecnologia": [
        "plataforma", "sistema", "software", "blockchain", "trazabilidad",
        "sensor", "iot", "ia", "equipo", "maquinaria", "trituracion",
        "tecnologia", "app", "digital"
    ],
    "capacidad": [
        "laboratorio", "investigacion", "i+d", "universidad", "centro",
        "ensayo", "certificacion", "acreditacion", "capacidad instalada",
        "caracterizacion"
    ],
    "servicio": [
        "recoleccion", "gestion", "logistica", "consultoria", "asesoria",
        "mantenimiento", "distribucion", "servicio", "implementacion",
        "lavado", "reciclaje", "instalacion"
    ],
    "bien": [
        "material", "materia prima", "pellet", "compost", "residuo",
        "producto", "arido", "fibra", "insumo", "bien", "venta", "compra"
    ],
}
 
SECTOR_KW = {
    "agua": [
        "agua", "acuifero", "tratamiento", "riego", "vertimiento",
        "hidrologico", "efluente", "humedal", "cuenca", "hidrico"
    ],
    "agro": [
        "agro", "alimento", "organico", "compost", "biorresiduos",
        "agricola", "alimentario", "humus", "lombriz", "ganadero",
        "cafe", "cacao", "hortalizas", "frutas", "verduras"
    ],
    "textil": [
        "textil", "fibra", "ropa", "moda", "upcycling", "confeccion",
        "tela", "hilo", "vestimenta", "cuero", "calzado"
    ],
    "construccion": [
        "construccion", "demolicion", "rcd", "arido", "concreto",
        "cemento", "obra", "edificio", "infraestructura", "yeso",
        "madera", "bambu"
    ],
    "residuos": [
        "residuo", "reciclaje", "plastico", "papel", "carton",
        "electronico", "respel", "basura", "aprovechamiento",
        "punto ecologico", "reciclado", "caneca"
    ],
}
 
ESTRATEGIA_KW = {
    "Diseno circular": [
        "diseno", "ecodiseno", "trazabilidad", "ciclo de vida",
        "blockchain", "sostenible", "ecologico", "verde"
    ],
    "Extension vida util": [
        "reparacion", "reutilizacion", "segunda vida", "refabricacion",
        "mantenimiento", "extension vida", "upcycling"
    ],
    "Valorizacion y cierre": [
        "reciclaje", "valorizacion", "compostaje", "biogas", "pellet",
        "recuperacion", "cierre de ciclo", "recoleccion", "aprovechamiento"
    ],
    "Modelos de negocio": [
        "plataforma", "marketplace", "startup", "escala", "fondo",
        "inversion", "capital", "financiacion", "vitrina", "emprendimiento"
    ],
    "Simbiosis industrial": [
        "simbiosis", "industrial", "subproducto",
        "intercambio de materiales", "residuo de uno"
    ],
}
 
 
def _score(text, kw_dict):
    tl = text.lower()
    scores = {k: sum(1 for w in v if w in tl) for k, v in kw_dict.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None
 
 
def classify(title="", desc=""):
    combined = f"{title} {desc}"
    return {
        "tipo":       _score(combined, TIPO_KW)       or "servicio",
        "sector":     _score(combined, SECTOR_KW)     or "residuos",
        "estrategia": _score(combined, ESTRATEGIA_KW) or "Valorizacion y cierre",
    }
 
 
def make_record(title, actor, source, desc,
                loc="Bogota D.C.", anno=None, url="", detail="", **extra):
    """Construye un registro normalizado."""
    cl = classify(title, desc)
    return {
        **cl,
        **extra,
        "title":      title.strip()[:220],
        "actor":      actor.strip()[:120],
        "source":     source,
        "url":        url,
        "desc":       desc.strip()[:240],
        "detail":     detail.strip()[:600],
        "loc":        loc,
        "ano":        str(anno or datetime.now().year),
        "scraped_at": TODAY,
    }
 
 
# ════════════════════════════════════════════════════════════════════════════
# FUENTE 1 — BAZZARBOG (PrestaShop HTML)
# ════════════════════════════════════════════════════════════════════════════
 
BAZZARBOG_KEYWORDS = [
    "reciclado", "sostenible", "ecologico", "organico",
    "biodegradable", "economia circular", "upcycling", "bambu",
    "compostable", "reutilizable", "bioplastico", "verde",
]
 
 
def scrape_bazzarbog(client):
    SOURCE    = "BazzarBog CCB"
    BASE      = "https://www.bazzarbog.com"
    seen_urls = set()
    results   = []
 
    for kw in BAZZARBOG_KEYWORDS:
        try:
            url = f"{BASE}/busqueda?s={kw}"
            log.info(f"[BazzarBog] Buscando: '{kw}'")
            res = client.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")
 
            cards = soup.select(
                "article.product-miniature, .product-miniature, .js-product"
            )
 
            for card in cards:
                link_el  = card.select_one(
                    "a.product-thumbnail, h3 a, h2 a, .product-title a"
                )
                title_el = card.select_one(".product-title, h3, h2")
                price_el = card.select_one(".price, .product-price")
                brand_el = card.select_one(
                    ".product-manufacturer, .brand, .manufacturer"
                )
 
                if not link_el:
                    continue
 
                product_url = link_el.get("href", "")
                if not product_url.startswith("http"):
                    product_url = BASE + product_url
 
                if product_url in seen_urls:
                    continue
                seen_urls.add(product_url)
 
                title = (
                    title_el.get_text(strip=True) if title_el
                    else link_el.get_text(strip=True) or "Producto sostenible"
                )
                actor = (
                    brand_el.get_text(strip=True) if brand_el
                    else "BazzarBog CCB"
                )
                price = price_el.get_text(strip=True) if price_el else ""
 
                detail_text = ""
                if len(results) < 60:
                    detail_text = _bazzarbog_detail(client, product_url)
 
                desc = (
                    detail_text[:240] if detail_text
                    else f"Producto sostenible: {title}. {price}"
                )
 
                results.append(make_record(
                    title=title, actor=actor, source=SOURCE,
                    desc=desc, detail=detail_text, url=product_url,
                ))
 
        except Exception as e:
            log.warning(f"[BazzarBog] Error keyword '{kw}': {e}")
 
    log.info(f"[BazzarBog] {len(results)} productos extraidos")
    return results
 
 
def _bazzarbog_detail(client, url):
    try:
        res  = client.get(url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(res.text, "lxml")
        el   = (
            soup.select_one(".product-description-short")
            or soup.select_one("#product-description-short")
            or soup.select_one(".product-description")
            or soup.select_one('[itemprop="description"]')
        )
        if el:
            return " ".join(el.get_text(separator=" ", strip=True).split())[:600]
    except Exception:
        pass
    return ""
 
 
# ════════════════════════════════════════════════════════════════════════════
# FUENTE 2 — ECMARKETPLACE LATAM
# ════════════════════════════════════════════════════════════════════════════
 
def scrape_ecmarketplace(client):
    SOURCE  = "ECMarketplace Latam"
    BASE    = "https://ecmarketplacelatam.com"
    results = []
 
    api_endpoints = [
        f"{BASE}/Buscador/ObtenerProductos",
        f"{BASE}/Buscador/GetProductos",
        f"{BASE}/api/productos",
        f"{BASE}/Buscador/Buscar",
    ]
 
    for endpoint in api_endpoints:
        try:
            log.info(f"[ECMarketplace] Intentando API: {endpoint}")
            for page in range(1, 6):
                payload = {
                    "pagina": page,
                    "ciudad": "",
                    "sectorEconomico": "",
                    "palabraClave": "",
                    "cantidadPorPagina": 20,
                }
                res = client.post(endpoint, json=payload, timeout=REQUEST_TIMEOUT)
                if res.status_code != 200:
                    break
 
                data  = res.json()
                items = (
                    data.get("productos") or data.get("data") or
                    data.get("items") or
                    (data if isinstance(data, list) else [])
                )
                if not items:
                    break
 
                for item in items:
                    title   = (item.get("nombre") or item.get("titulo") or
                               item.get("name") or "Sin nombre")
                    actor   = (item.get("empresa") or item.get("actor") or
                               item.get("proveedor") or title)
                    desc    = (item.get("descripcion") or item.get("description") or "")
                    ciudad  = item.get("ciudad") or "Bogota"
                    sector  = item.get("sectorEconomico") or ""
                    item_id = item.get("id") or item.get("idProducto") or ""
                    iurl    = f"{BASE}/Buscador/Detalle/{item_id}" if item_id else BASE
 
                    results.append(make_record(
                        title=title, actor=actor, source=SOURCE,
                        desc=f"{desc} {sector}".strip(),
                        url=iurl, loc=ciudad,
                    ))
 
            if results:
                log.info(f"[ECMarketplace] API funciono: {len(results)} registros")
                return results
 
        except Exception as e:
            log.debug(f"[ECMarketplace] Endpoint {endpoint} fallo: {e}")
 
    # Fallback HTML
    log.info("[ECMarketplace] Fallback: scraping HTML")
    try:
        res  = client.get(f"{BASE}/Buscador", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(res.text, "lxml")
        cards = soup.select(
            ".card, .producto, .oferta, .item-marketplace, [class*='product']"
        )
        for card in cards[:50]:
            title_el = card.select_one("h2, h3, h4, .titulo, .nombre, strong")
            desc_el  = card.select_one("p, .descripcion, .description")
            link_el  = card.select_one("a[href]")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            desc  = desc_el.get_text(strip=True) if desc_el else ""
            href  = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                href = BASE + href
            results.append(make_record(
                title=title, actor=title, source=SOURCE,
                desc=desc, url=href,
            ))
    except Exception as e:
        log.error(f"[ECMarketplace] Fallback HTML error: {e}")
 
    log.info(f"[ECMarketplace] {len(results)} registros extraidos")
    return results
 
 
# ════════════════════════════════════════════════════════════════════════════
# FUENTE 3 — NEGOCIOS VERDES CAR
# ════════════════════════════════════════════════════════════════════════════
 
def scrape_negocios_verdes_car(client_no_ssl):
    SOURCE  = "Ventanilla Negocios Verdes CAR"
    BASE    = "https://negociosverdes.car.gov.co"
    results = []
 
    list_urls = [
        f"{BASE}/",
        f"{BASE}/directorio",
        f"{BASE}/empresas",
        f"{BASE}/productos",
        f"{BASE}/negocios",
        f"{BASE}/catalogo",
    ]
 
    for url in list_urls:
        try:
            log.info(f"[NegociosVerdes CAR] GET {url}")
            res = client_no_ssl.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            soup  = BeautifulSoup(res.text, "lxml")
            cards = soup.select(
                ".empresa, .negocio, .producto, .card, article, "
                ".item, li.result, .directorio-item"
            )
            if not cards:
                cards = soup.select("main li, .content li, #content li")
 
            for card in cards[:80]:
                title_el = card.select_one(
                    "h1,h2,h3,h4,strong,.nombre,.title,.name"
                )
                desc_el  = card.select_one(
                    "p,.descripcion,.description,.resumen"
                )
                link_el  = card.select_one("a[href]")
                loc_el   = card.select_one(
                    ".municipio,.ciudad,.location,.ubicacion"
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if len(title) < 4:
                    continue
                desc  = desc_el.get_text(strip=True)  if desc_el  else ""
                loc   = loc_el.get_text(strip=True)   if loc_el   else "Cundinamarca"
                href  = link_el["href"]                if link_el  else ""
                if href and href.startswith("/"):
                    href = BASE + href
 
                results.append(make_record(
                    title=title, actor=title, source=SOURCE,
                    desc=desc, url=href, loc=loc,
                    tipo="servicio",
                ))
 
            if results:
                break
 
        except httpx.HTTPStatusError:
            continue
        except Exception as e:
            log.debug(f"[NegociosVerdes CAR] {url}: {e}")
 
    log.info(f"[NegociosVerdes CAR] {len(results)} registros extraidos")
    return results
 
 
# ════════════════════════════════════════════════════════════════════════════
# FUENTE 4 — HEMA ATENEA (Drupal 10)
# ════════════════════════════════════════════════════════════════════════════
 
def scrape_hema_atenea(client):
    SOURCE  = "HEMA - Atenea"
    BASE    = "https://www.agenciaatenea.gov.co"
    results = []
 
    sections = [
        (f"{BASE}/hema/ofertas-de-servicio", "servicio",  "CTeI / I+D"),
        (f"{BASE}/hema/capacidades",          "capacidad", "CTeI / I+D"),
    ]
 
    for url, tipo_default, sector_hint in sections:
        try:
            log.info(f"[HEMA] GET {url}")
            res  = client.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")
 
            cards = (
                soup.select(".views-row") or
                soup.select("article.node") or
                soup.select(".view-content > div") or
                soup.select("main .field-items > .field-item")
            )
 
            for card in cards[:60]:
                title_el = card.select_one(
                    "h2,h3,.field--name-title,a.node-link"
                )
                desc_el  = (
                    card.select_one(
                        ".field--name-body,.field--name-field-descripcion,p"
                    ) or card.select_one(".teaser-body,.description")
                )
                actor_el = card.select_one(
                    ".field--name-field-actor,"
                    ".field--name-field-empresa,"
                    ".field--name-field-entidad"
                )
                link_el  = card.select_one("a[href*='/hema/'], a.node-link")
 
                if not title_el:
                    continue
 
                title = title_el.get_text(strip=True)
                desc  = desc_el.get_text(strip=True)  if desc_el  else ""
                actor = (
                    actor_el.get_text(strip=True) if actor_el
                    else "Ecosistema CTeI Bogota"
                )
                href = ""
                if link_el:
                    href = link_el["href"]
                    if href.startswith("/"):
                        href = BASE + href
 
                results.append(make_record(
                    title=title, actor=actor, source=SOURCE,
                    desc=desc or f"{tipo_default.title()} - {sector_hint}",
                    url=href,
                    tipo=tipo_default,
                    sector="residuos",
                ))
 
        except Exception as e:
            log.warning(f"[HEMA] Error en {url}: {e}")
 
    log.info(f"[HEMA] {len(results)} registros extraidos")
    return results
 
 
# ════════════════════════════════════════════════════════════════════════════
# FUENTE 5 — ECODIRECTORIO SDA (Google Sites - Playwright)
# ════════════════════════════════════════════════════════════════════════════
 
def scrape_ecodirectorio_sda():
    SOURCE  = "Ecodirectorio SDA"
    URL     = "https://sites.google.com/ambientebogota.gov.co/ecodirectorio-2023/inicio"
    results = []
 
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("[Ecodirectorio SDA] Playwright no instalado. Skipping.")
        return []
 
    try:
        log.info(f"[Ecodirectorio SDA] Lanzando Playwright -> {URL}")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            page.goto(URL, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(3000)
 
            links = page.query_selector_all("a[href*='ecodirectorio']")
            company_urls = list({
                el.get_attribute("href") for el in links
                if el.get_attribute("href")
                and "inicio" not in el.get_attribute("href")
            })[:40]
 
            log.info(f"[Ecodirectorio SDA] {len(company_urls)} perfiles encontrados")
 
            for company_url in company_urls:
                try:
                    page.goto(
                        company_url, wait_until="domcontentloaded", timeout=20000
                    )
                    page.wait_for_timeout(1500)
 
                    title = page.title().replace(
                        " - Ecodirectorio 2023", ""
                    ).strip()
                    body_text  = page.inner_text("main, article, .tyJCtd") or ""
                    body_clean = " ".join(body_text.split())[:600]
 
                    results.append(make_record(
                        title=title or "Actor ecodirectorio",
                        actor=title or "Actor ecodirectorio",
                        source=SOURCE,
                        desc=body_clean[:240],
                        detail=body_clean,
                        url=company_url,
                    ))
                except Exception as e:
                    log.debug(f"[Ecodirectorio SDA] Error en {company_url}: {e}")
 
            browser.close()
 
    except Exception as e:
        log.error(f"[Ecodirectorio SDA] Error general: {e}")
 
    log.info(f"[Ecodirectorio SDA] {len(results)} registros extraidos")
    return results
 
 
# ════════════════════════════════════════════════════════════════════════════
# POST-PROCESAMIENTO
# ════════════════════════════════════════════════════════════════════════════
 
VALID_TIPOS    = {"bien", "servicio", "tecnologia", "capacidad", "financiacion"}
VALID_SECTORES = {"agro", "construccion", "textil", "residuos", "agua"}
 
 
def validate(records):
    clean = []
    for r in records:
        if not r.get("title", "").strip() or len(r["title"]) < 3:
            continue
        if r.get("tipo")   not in VALID_TIPOS:    r["tipo"]   = "servicio"
        if r.get("sector") not in VALID_SECTORES: r["sector"] = "residuos"
        if not r.get("desc", "").strip():          r["desc"]   = r["title"]
        clean.append(r)
    return clean
 
 
def deduplicate(records):
    seen = {}
    for r in records:
        key = re.sub(r"\s+", " ", r["title"].lower().strip())[:80]
        if (key not in seen or
                len(r.get("detail", "")) > len(seen[key].get("detail", ""))):
            seen[key] = r
    result = list(seen.values())
    log.info(f"[Dedup] {len(records)} -> {len(result)} registros unicos")
    return result
 
 
# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
 
def main():
    log.info("=" * 55)
    log.info("BRC Metabuscador - Inicio de scraping")
    log.info(f"Hora UTC: {TODAY}")
    log.info("=" * 55)
 
    all_records = []
 
    # Cliente HTTP estandar
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        all_records += scrape_bazzarbog(client)
        all_records += scrape_ecmarketplace(client)
        all_records += scrape_hema_atenea(client)
 
    # Cliente sin verificacion SSL (portales gov.co con cert. problematico)
    with httpx.Client(headers=HEADERS, follow_redirects=True, verify=False) as client_no_ssl:
        all_records += scrape_negocios_verdes_car(client_no_ssl)
 
    # Playwright
    all_records += scrape_ecodirectorio_sda()
 
    # Post-proceso
    all_records = validate(all_records)
    all_records = deduplicate(all_records)
    all_records.sort(key=lambda r: (r["source"], r["title"]))
 
    OUTPUT_FILE.write_text(
        json.dumps(all_records, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
 
    log.info("=" * 55)
    log.info(f"data.json -> {len(all_records)} registros")
    log.info(f"Fuentes: {', '.join({r['source'] for r in all_records})}")
    log.info(f"Archivo: {OUTPUT_FILE.resolve()}")
    log.info("=" * 55)
 
 
if __name__ == "__main__":
    main()
