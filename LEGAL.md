# Consideraciones legales y éticas — LEER ANTES DE OPERAR

## Resumen ejecutivo

Este proyecto extrae **datos públicamente visibles** de un sitio de cams
(listados de salas, contadores de viewers, eventos de tip del chat público).
Sin embargo, "públicamente visible" ≠ "libre de restricciones". Antes de
operar en producción, revisa lo siguiente con un abogado de tu jurisdicción.

## 1. Términos de Servicio (ToS) del sitio

- La mayoría de cam sites **prohíben explícitamente** el scraping en sus ToS.
  Leerlos es obligatorio antes de operar.
- Violar ToS puede ser **causa de ban** de tu cuenta/IP y, en algunas
  jurisdicciones, base para demandas civiles (breach of contract) o penales
  (CFAA en EE. UU., ley de acceso abusivo en varios países de LATAM).
- **Alternativa legítima**: muchos cam sites ofrecen un **programa de afiliados
  oficial** con API de stats. Si existe, ÚSALO en vez de scraping. El campo
  `BOOST_AFFILIATE_ID` está pensado para esto.

## 2. robots.txt

- Respeta `robots.txt`. Si prohíbe `/list` o el path que scrapeas, **no lo hagas**.
- En `scraper/http_utils.py` ya hay un delay conservador; mantenlo ≥ 2s.

## 3. Datos personales y privacidad

- Los **usernames de tippers** son datos personales bajo GDPR (UE), LGPD (Brasil),
  Ley 25.326 (Argentina), Ley 1581 (Colombia), etc.
- Recomendaciones:
  - **No publiques** usernames crudos de tippers en el dashboard público. El
    leaderboard actual los muestra; considera cambiar `tipper_username` por
    `tipper_hash` en `/api/top-tippers` si el dashboard es público.
  - **No cruces** los datos con redes sociales para deanonymizar.
  - **Retención**: aplica una política de borrado (ej. tips > 90 días se agregan
    y se borra el detalle). Añade un job: `DELETE FROM tip_events WHERE occurred_at < now() - INTERVAL '90 days'`.
  - **Opt-out**: ofrece un endpoint `/api/opt-out?username=X` que anonimice los
    datos de un usuario que lo pida.

## 4. Contenido adulto y edad

- El sitio objetivo contiene contenido adulto. **No extraigas ni almacenes**
  imágenes/videos/thumbnails. El scraper solo guarda texto y métricas.
- `thumbnail_url` en el modelo se guarda pero el dashboard **no la renderiza**
  por defecto. Mantenlo así.
- Verifica que las modelos estén verificadas +18 según el sitio; no agregues
  lógica de "edad estimada".

## 5. No harassment / no doxxing

- **PROHIBIDO** usar los datos para:
  - Contactar tippers individualmente (DM, email, WhatsApp).
  - Publicar listas de "deudores" o rankings shaming.
  - Doxxear (nombre real, ubicación, redes) a modelos o tippers.
- El proyecto está pensado para **analytics de mercado y optimización de
  tráfico**, no para vigilancia individual.

## 6. Propiedad intelectual

- No redistribuyas el contenido del sitio (screenshots, bios completas, etc.).
- El dashboard debe mostrar **métricas agregadas** (números, rankings), no
  contenido del sitio reproducido.

## 7. Affiliate vs scraping

La vía **más limpia** de "boostear tráfico" es el programa de afiliados oficial
del sitio. Si existe:
1. Regístrate como afiliado.
2. Usa `BOOST_AFFILIATE_ID` para generar links `?ref=TU_ID`.
3. El dashboard recomienda cross-promo con links de afiliado.
4. Mides conversiones con el pixel `/p/track` (event=conversion, firmado HMAC).
5. Monetizas comisiones — sin scraping, sin riesgo legal.

Considera desactivar el scraper y operar solo con afiliación + pixel si el
sitio ofrece API de afiliados.

## 8. Jurisdicción

- Colombia (tu zona horaria configurada): aplica la **Ley 1581 de 2012**
  (protección de datos personales) y el **Estatuto del Consumidor**.
- Registrar tu base de datos ante la SIC si vas a tratar datos personales
  a escala puede ser necesario. Consulta a un abogado.
- Si el sitio está en EE. UU., aplica **18 U.S.C. § 1030 (CFAA)** y leyes
  estatales (e.g. California CDBA).

## 9. Recomendación final

> **Consentimiento explícito > Afiliación oficial > Scraping ético > No operar.**

Si tienes duda razonable sobre la legalidad de alguna feature, **no la
implementes**. El código de este repo es un marco técnico; la
responsabilidad del uso es del operador.
