"""
scraper.py â€” BRC Metabuscador
Extrae datos reales de portales colombianos de economÃ­a circular/verde
y genera data.json para el front-end estÃ¡tico.

Fuentes implementadas:
  1. BazzarBog CCB          â€” HTML estÃ¡tico (PrestaShop), alta confiabilidad
  2. ECMarketplace Latam    â€” API interna JSON, alta confiabilidad
  3. Negocios Verdes CAR    â€” HTML estÃ¡tico, alta confiabilidad
  4. HEMA Atenea            â€” HTML Drupal (actores + servicios), media confiabilidad
  5. Ecodirectorio SDA      â€” Google Sites (JS), requiere Playwright

Instalar:
  pip install httpx beautifulsoup4 lxml playwright
  playwright install chromium   â† solo para fuente SDA
"""

import json
import re
import logging
import asyncio
import ssl
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# â”€â”€ CONFIG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
OUTPUT_FILE   = Path(__file__).parent / "data.json"
LOG_FORMAT    = "%(asctime)s [%(levelname)s] %(message)s"
REQUEST_TIMEOUT = 25
TODAY         = datetime.now(timezone.utc).isoformat()

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("brc-scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BRC-Metabuscador/2.0; "
        "+https://bogotacircular-collab.github.io/brc-metabuscador)"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

# â”€â”€ CLASIFICADOR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

TIPO_KW = {
    "financiacion": ["fondo","crÃ©dito","financiaciÃ³n","convocatoria","beca","inversiÃ³n","capital","lÃ­nea verde","prÃ©stamo"],
    "tecnologia":   ["plataforma","sistema","software","blockchain","trazabilidad","sensor","iot","ia","equipo","maquinaria","trituraciÃ³n","tecnologÃ­a","app","digital"],
    "capacidad":    ["laboratorio","investigaciÃ³n","i+d","universidad","centro","ensayo","certificaciÃ³n","acreditaciÃ³n","capacidad instalada","caracterizaciÃ³n"],
    "servicio":     ["recolecciÃ³n","gestiÃ³n","logÃ­stica","consultorÃ­a","asesorÃ­a","mantenimiento","distribuciÃ³n","servicio","implementaciÃ³n","lavado","reciclaje","instalaciÃ³n"],
    "bien":         ["material","materia prima","pellet","compost","residuo","producto","Ã¡rido","fibra","insumo","bien","venta","compra"],
}

SECTOR_KW = {
    "agua":        ["agua","acuÃ­fero","tratamiento","riego","vertimiento","hidrolÃ³gico","efluente","humedal","cuenca","hÃ­drico"],
    "agro":        ["agro","alimento","orgÃ¡nico","compost","biorresiduos","agrÃ­cola","alimentario","humus","lombriz","ganadero","cafÃ©","cacao","hortalizas","frutas","verduras"],
    "textil":      ["textil","fibra","ropa","moda","upcycling","confecciÃ³n","tela","hilo","vestimenta","cuero","calzado"],
    "construccion":["construcciÃ³n","demoliciÃ³n","rcd","Ã¡rido","concreto","cemento","obra","edificio","infraestructura","yeso","madera","bambÃº"],
    "residuos":    ["residuo","reciclaje","plÃ¡stico","papel","cartÃ³n","electrÃ³nico","respel","basura","aprovechamiento","punto ecolÃ³gico","reciclado","caneca"],
}

ESTRATEGIA_KW = {
    "DiseÃ±o circular":       ["diseÃ±o","ecodiseÃ±o","trazabilidad","ciclo de vida","blockchain","sostenible","ecolÃ³gico","verde"],
    "ExtensiÃ³n vida Ãºtil":   ["reparaciÃ³n","reutilizaciÃ³n","segunda vida","refabricaciÃ³n","mantenimiento","extensiÃ³n vida","upcycling"],
    "ValorizaciÃ³n y cierre": ["reciclaje","valorizaciÃ³n","compostaje","biogÃ¡s","pellet","recuperaciÃ³n","cierre de ciclo","recolecciÃ³n","aprovechamiento"],
    "Modelos de negocio":    ["plataforma","marketplace","startup","escala","fondo","inversiÃ³n","capital","financiaciÃ³n","vitrina","emprendimiento"],
    "Simbiosis industrial":  ["simbiosis","industrial","subproducto","intercambio de materiales","residuo de uno"],
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
        "estrategia": _score(combined, ESTRATEGIA_KW) or "ValorizaciÃ³n y cierre",
    }

def record(title, actor, source, desc, loc="BogotÃ¡ D.C.", aÃ±o=None, url="", detail="", **extra):
    """Construye un registro normalizado."""
    cl = classify(title, desc)
    return {
        **cl,
        **extra,          # permite sobreescribir tipo/sector si ya se conoce
        "title":      title.strip()[:220],
        "actor":      actor.strip()[:120],
        "source":     source,
        "url":        url,
        "desc":       desc.strip()[:240],
        "detail":     detail.strip()[:600],
        "loc":        loc,
        "aÃ±o":        str(aÃ±o or datetime.now().year),
        "scraped_at": TODAY,
    }

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FUENTE 1 â€” BAZZARBOG (PrestaShop HTML)
# Estrategia: buscar por keywords de economÃ­a circular/verde en el buscador
# URL del buscador: https://www.bazzarbog.com/busqueda?s=KEYWORD
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

BAZZARBOG_KEYWORDS = [
    "reciclado", "sostenible", "ecolÃ³gico", "orgÃ¡nico",
    "biodegradable", "economÃ­a circular", "upcycling", "bambÃº",
    "compostable", "reutilizable", "bioplÃ¡stico", "verde",
]

def scrape_bazzarbog(client: httpx.Client) -> list[dict]:
    SOURCE = "BazzarBog CCB"
    BASE   = "https://www.bazzarbog.com"
    seen_urls = set()
    results   = []

    for kw in BAZZARBOG_KEYWORDS:
        try:
            url = f"{BASE}/busqueda?s={kw}"
            log.info(f"[BazzarBog] Buscando: '{kw}'")
            res = client.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")

            # PrestaShop: productos en .product-miniature o article.product-miniature
            cards = soup.select("article.product-miniature, .product-miniature, .js-product")

            for card in cards:
                link_el  = card.select_one("a.product-thumbnail, h3 a, h2 a, .product-title a")
                title_el = card.select_one(".product-title, h3, h2")
                price_el = card.select_one(".price, .product-price")
                brand_el = card.select_one(".product-manufacturer, .brand, .manufacturer")

                if not link_el:
                    continue

                product_url  = link_el.get("href", "")
                if not product_url.startswith("http"):
                    product_url = BASE + product_url

                if product_url in seen_urls:
                    continue
                seen_urls.add(product_url)

                title = (title_el.get_text(strip=True) if title_el
                         else link_el.get_text(strip=True) or "Producto sostenible")
                actor = (brand_el.get_text(strip=True) if brand_el else "BazzarBog CCB")
                price = price_el.get_text(strip=True) if price_el else ""

                # Obtener descripciÃ³n de la pÃ¡gina de detalle (las primeras 5 por keyword)
                detail_text = ""
                if len(results) < 60:
                    detail_text = _fetch_bazzarbog_detail(client, product_url, BASE)

                desc = detail_text[:240] if detail_text else f"Producto sostenible: {title}. {price}"

                results.append(record(
                    title=title, actor=actor, source=SOURCE,
                    desc=desc, detail=detail_text, url=product_url,
                    loc="BogotÃ¡ D.C."
                ))

        except Exception as e:
            log.warning(f"[BazzarBog] Error con keyword '{kw}': {e}")

    log.info(f"[BazzarBog] âœ“ {len(results)} productos extraÃ­dos")
    return results


def _fetch_bazzarbog_detail(client, url, base):
    """Extrae descripciÃ³n corta del producto desde su pÃ¡gina."""
    try:
        res = client.get(url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(res.text, "lxml")
        # PrestaShop: descripciÃ³n corta en .product-description-short o #product-description-short
        desc_el = (soup.select_one(".product-description-short")
                   or soup.select_one("#product-description-short")
                   or soup.select_one(".product-description")
                   or soup.select_one('[itemprop="description"]'))
        if desc_el:
            return " ".join(desc_el.get_text(separator=" ", strip=True).split())[:600]
    except Exception:
        pass
    return ""


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FUENTE 2 â€” ECMARKETPLACE LATAM (API interna JSON)
# Estrategia: la web carga productos via POST/GET a un endpoint interno.
# URL API descubierta: /Buscador/ObtenerProductos o similar.
# Fallback: scraping del HTML del buscador con paginaciÃ³n.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def scrape_ecmarketplace(client: httpx.Client) -> list[dict]:
    SOURCE = "ECMarketplace Latam"
    BASE   = "https://ecmarketplacelatam.com"
    results = []

    # Intentar API interna (endpoint tÃ­pico de ASP.NET MVC con paginaciÃ³n)
    api_endpoints = [
        f"{BASE}/Buscador/ObtenerProductos",
        f"{BASE}/Buscador/GetProductos",
        f"{BASE}/api/productos",
        f"{BASE}/Buscador/Buscar",
    ]

    for endpoint in api_endpoints:
        try:
            log.info(f"[ECMarketplace] Intentando API: {endpoint}")
            # Payload tÃ­pico de este tipo de plataformas ASP.NET
            for page in range(1, 6):  # hasta 5 pÃ¡ginas
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

                data = res.json()
                items = (data.get("productos") or data.get("data") or
                         data.get("items") or data if isinstance(data, list) else [])

                if not items:
                    break

                for item in items:
                    title  = (item.get("nombre") or item.get("titulo") or
                              item.get("name") or "Sin nombre")
                    actor  = (item.get("empresa") or item.get("actor") or
                              item.get("proveedor") or item.get("vendedor") or title)
                    desc   = (item.get("descripcion") or item.get("description") or
                              item.get("resumen") or "")
                    ciudad = (item.get("ciudad") or item.get("city") or "BogotÃ¡")
                    sector = item.get("sectorEconomico") or item.get("sector") or ""
                    item_id = item.get("id") or item.get("idProducto") or ""
                    url    = f"{BASE}/Buscador/Detalle/{item_id}" if item_id else BASE

                    results.append(record(
                        title=title, actor=actor, source=SOURCE,
                        desc=f"{desc} {sector}".strip(), url=url, loc=ciudad,
                    ))

            if results:
                log.info(f"[ECMarketplace] âœ“ API funcionÃ³: {len(results)} registros")
                return results

        except Exception as e:
            log.debug(f"[ECMarketplace] Endpoint {endpoint} fallÃ³: {e}")

    # â”€â”€ FALLBACK: Scraping HTML del buscador â”€â”€
    log.info("[ECMarketplace] Fallback: scraping HTML del buscador")
    try:
        res = client.get(f"{BASE}/Buscador", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(res.text, "lxml")

        # Buscar tarjetas de productos en el HTML renderizado del servidor
        cards = soup.select(".card, .producto, .oferta, .item-marketplace, [class*='product']")
        for card in cards[:50]:
            title_el = card.select_one("h2, h3, h4, .titulo, .nombre, strong")
            desc_el  = card.select_one("p, .descripcion, .description")
            link_el  = card.select_one("a[href]")

            if not title_el:
                continue

            title   = title_el.get_text(strip=True)
            desc    = desc_el.get_text(strip=True) if desc_el else ""
            href    = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                href = BASE + href

            results.append(record(
                title=title, actor=title, source=SOURCE,
                desc=desc, url=href, loc="BogotÃ¡ D.C."
            ))

    except Exception as e:
        log.error(f"[ECMarketplace] Error en fallback HTML: {e}")

    log.info(f"[ECMarketplace] âœ“ {len(results)} registros extraÃ­dos")
    return results


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FUENTE 3 â€” NEGOCIOS VERDES CAR
# Estrategia: HTML estÃ¡tico, ignorar SSL (cert. autofirmado del gov.co)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def scrape_negocios_verdes_car(client_no_ssl: httpx.Client) -> list[dict]:
    SOURCE = "Ventanilla Negocios Verdes CAR"
    BASE   = "https://negociosverdes.car.gov.co"
    results = []

    # PÃ¡ginas tÃ­picas de un directorio de negocios verdes
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
            soup = BeautifulSoup(res.text, "lxml")

            # Buscar tarjetas / listado de empresas o productos verdes
            cards = soup.select(
                ".empresa, .negocio, .producto, .card, article, "
                ".item, li.result, .directorio-item"
            )

            if not cards:
                # Intentar extracciÃ³n genÃ©rica de listas
                cards = soup.select("main li, .content li, #content li")

            for card in cards[:80]:
                title_el = card.select_one("h1,h2,h3,h4,strong,.nombre,.title,.name")
                desc_el  = card.select_one("p,.descripcion,.description,.resumen")
                link_el  = card.select_one("a[href]")
                loc_el   = card.select_one(".municipio,.ciudad,.location,.ubicacion")

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

                results.append(record(
                    title=title, actor=title, source=SOURCE,
                    desc=desc, url=href, loc=loc,
                    tipo="servicio",   # Negocios Verdes: mayorÃ­a son servicios
                ))

            if results:
                break   # Si ya encontramos en esta URL, no seguir

        except httpx.HTTPStatusError:
            continue
        except Exception as e:
            log.debug(f"[NegociosVerdes CAR] {url}: {e}")

    log.info(f"[NegociosVerdes CAR] âœ“ {len(results)} registros extraÃ­dos")
    return results


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FUENTE 4 â€” HEMA ATENEA (Drupal 10, HTML estÃ¡tico parcial)
# Estrategia: la lista de actores estÃ¡ en Power BI (imposible), pero
# la secciÃ³n "Oferta de servicios" y "Capacidades" sÃ­ tiene HTML scrapeble.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def scrape_hema_atenea(client: httpx.Client) -> list[dict]:
    SOURCE = "HEMA â€” Atenea"
    BASE   = "https://www.agenciaatenea.gov.co"
    results = []

    sections = [
        (f"{BASE}/hema/ofertas-de-servicio", "servicio", "CTeI / I+D"),
        (f"{BASE}/hema/capacidades",         "capacidad", "CTeI / I+D"),
    ]

    for url, tipo_default, sector_hint in sections:
        try:
            log.info(f"[HEMA] GET {url}")
            res = client.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")

            # Drupal: artÃ­culos en .views-row, article, .node, .view-content > div
            cards = (soup.select(".views-row") or
                     soup.select("article.node") or
                     soup.select(".view-content > div") or
                     soup.select("main .field-items > .field-item"))

            for card in cards[:60]:
                title_el = card.select_one("h2,h3,.field--name-title,a.node-link")
                desc_el  = (card.select_one(".field--name-body,.field--name-field-descripcion,p")
                            or card.select_one(".teaser-body,.description"))
                actor_el = card.select_one(".field--name-field-actor,.field--name-field-empresa,.field--name-field-entidad")
                link_el  = card.select_one("a[href*='/hema/'], a.node-link")

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                desc  = desc_el.get_text(strip=True)  if desc_el  else ""
                actor = actor_el.get_text(strip=True) if actor_el else "Ecosistema CTeI BogotÃ¡"
                href  = ""
                if link_el:
                    href = link_el["href"]
                    if href.startswith("/"):
                        href = BASE + href

                results.append(record(
                    title=title, actor=actor, source=SOURCE,
                    desc=desc or f"{tipo_default.title()} â€” {sector_hint}",
                    url=href, loc="BogotÃ¡ D.C.",
                    tipo=tipo_default,
                    sector="capacidad",
                ))

        except Exception as e:
            log.warning(f"[HEMA] Error en {url}: {e}")

    log.info(f"[HEMA] âœ“ {len(results)} registros extraÃ­dos")
    return results


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FUENTE 5 â€” ECODIRECTORIO SDA (Google Sites â€” JS rendering)
# Estrategia: Playwright headless. Si no estÃ¡ disponible, skip graceful.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def scrape_ecodirectorio_sda() -> list[dict]:
    SOURCE = "Ecodirectorio SDA"
    URL    = "https://sites.google.com/ambientebogota.gov.co/ecodirectorio-2023/inicio"
    results = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("[Ecodirectorio SDA] Playwright no instalado. Skipping.")
        return []

    try:
        log.info(f"[Ecodirectorio SDA] Lanzando Playwright â†’ {URL}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page    = browser.new_page()
            page.goto(URL, wait_until="networkidle", timeout=45000)

            # Esperar que cargue el contenido dinÃ¡mico
            page.wait_for_timeout(3000)

            # Google Sites: buscar links a fichas de empresas
            # TÃ­picamente en cards con links a sub-pÃ¡ginas
            links = page.query_selector_all("a[href*='ecodirectorio']")
            company_urls = list({
                el.get_attribute("href") for el in links
                if el.get_attribute("href") and "inicio" not in el.get_attribute("href")
            })[:40]

            log.info(f"[Ecodirectorio SDA] Encontrados {len(company_urls)} perfiles")

            for company_url in company_urls:
                try:
                    page.goto(company_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)

                    title = page.title().replace(" â€” Ecodirectorio 2023", "").strip()
                    body_text = page.inner_text("main, article, .tyJCtd") or ""
                    body_clean = " ".join(body_text.split())[:600]

                    results.append(record(
                        title=title or "Actor ecodirectorio",
                        actor=title or "Actor ecodirectorio",
                        source=SOURCE,
                        desc=body_clean[:240],
                        detail=body_clean,
                        url=company_url,
                        loc="BogotÃ¡ D.C.",
                    ))
                except Exception as e:
                    log.debug(f"[Ecodirectorio SDA] Error en {company_url}: {e}")

            browser.close()

    except Exception as e:
        log.error(f"[Ecodirectorio SDA] Error general: {e}")

    log.info(f"[Ecodirectorio SDA] âœ“ {len(results)} registros extraÃ­dos")
    return results


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# POST-PROCESAMIENTO
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

VALID_TIPOS    = {"bien","servicio","tecnologia","capacidad","financiacion"}
VALID_SECTORES = {"agro","construccion","textil","residuos","agua"}

def validate(records):
    clean = []
    for r in records:
        if not r.get("title","").strip() or len(r["title"]) < 3:
            continue
        if r.get("tipo")   not in VALID_TIPOS:    r["tipo"]   = "servicio"
        if r.get("sector") not in VALID_SECTORES: r["sector"] = "residuos"
        if not r.get("desc","").strip():           r["desc"]   = r["title"]
        clean.append(r)
    return clean

def deduplicate(records):
    seen = {}
    for r in records:
        key = re.sub(r"\s+", " ", r["title"].lower().strip())[:80]
        if key not in seen or len(r.get("detail","")) > len(seen[key].get("detail","")):
            seen[key] = r
    result = list(seen.values())
    log.info(f"[Dedup] {len(records)} â†’ {len(result)} registros Ãºnicos")
    return result


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def main():
    log.info("â•" * 55)
    log.info("BRC Metabuscador â€” Inicio de scraping")
    log.info(f"Hora UTC: {TODAY}")
    log.info("â•" * 55)

    all_records = []

    # Cliente HTTP estÃ¡ndar
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        all_records += scrape_bazzarbog(client)
        all_records += scrape_ecmarketplace(client)
        all_records += scrape_hema_atenea(client)

    # Cliente HTTP sin verificaciÃ³n SSL (para CAR y portales gov.co con cert. problemÃ¡tico)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE
    with httpx.Client(headers=HEADERS, follow_redirects=True, verify=False) as client_no_ssl:
        all_records += scrape_negocios_verdes_car(client_no_ssl)

    # Playwright (requiere instalaciÃ³n adicional)
    all_records += scrape_ecodirectorio_sda()

    # Post-proceso
    all_records = validate(all_records)
    all_records = deduplicate(all_records)
    all_records.sort(key=lambda r: (r["source"], r["title"]))

    # Escribir JSON
    OUTPUT_FILE.write_text(
        json.dumps(all_records, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    log.info("â•" * 55)
    log.info(f"âœ“ data.json â†’ {len(all_records)} registros")
    log.info(f"  Fuentes: {', '.join({r['source'] for r in all_records})}")
    log.info(f"  Archivo: {OUTPUT_FILE.resolve()}")
    log.info("â•" * 55)


if __name__ == "__main__":
    main()
