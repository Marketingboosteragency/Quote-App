#!/usr/bin/env python3
"""
WebApp de Cotización y Extracción de Datos con IA (Versión Final con Imágenes en PDF)
Aplicación Flask que permite:
1. Generar plantillas de cotización profesionales con imágenes de producto.
2. Extraer datos de productos e imágenes de enlaces web de forma robusta.
3. Calcular impuestos de venta (Sales Tax) con el 7% de Florida como valor por defecto.
"""

from flask import Flask, render_template_string, request, jsonify, send_from_directory, url_for
from werkzeug.utils import secure_filename
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import uuid
from urllib.parse import urljoin, urlparse
import re
import traceback
import json

# Importar librerías para generar PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

# --- Configuración de la App Flask ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_por_defecto_para_desarrollo')

# --- CONFIGURACIÓN PARA RENDER ---
# Render montará el disco persistente en /mnt/data
# Usamos esta ruta base para todos los archivos que deben persistir.
PERSISTENT_DISK_PATH = '/mnt/data'
UPLOAD_FOLDER = os.path.join(PERSISTENT_DISK_PATH, 'uploads')
QUOTES_FOLDER = os.path.join(PERSISTENT_DISK_PATH, 'cotizaciones')

# Los directorios estáticos que están en el código (como el favicon) se mantienen igual.
STATIC_FOLDER = 'static'
STATIC_IMAGES_FOLDER = os.path.join(STATIC_FOLDER, 'images')

# Crear directorios necesarios al iniciar (Render lo hará con el build script)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QUOTES_FOLDER, exist_ok=True)
os.makedirs(STATIC_IMAGES_FOLDER, exist_ok=True) # Este puede quedarse, ya que es para imágenes extraídas temporalmente

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# --- Clase para Extracción de Datos Web (SIN CAMBIOS) ---
class WebDataExtractor:
    """Clase mejorada para extraer datos de páginas web, con cabeceras robustas y selectores específicos."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,es-ES;q=0.8,es;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1',
        })

    def _extract_product_details(self, soup):
        try:
            title_selectors = ['#productTitle', '.ui-pdp-title', 'h1']
            title = None
            for selector in title_selectors:
                title_element = soup.select_one(selector)
                if title_element:
                    title = title_element.get_text(strip=True)
                    break

            price_selectors = ['span.a-offscreen',
                               '.andes-money-amount.ui-pdp-price__part .andes-money-amount__fraction', '.a-price-whole']
            price = None
            for selector in price_selectors:
                price_element = soup.select_one(selector)
                if price_element:
                    price_str = price_element.get_text(strip=True)
                    cleaned_price_str = re.sub(r'[^\d,.]', '', price_str)
                    if ',' in cleaned_price_str and '.' in cleaned_price_str:
                        cleaned_price_str = cleaned_price_str.replace('.', '').replace(',', '.')
                    else:
                        cleaned_price_str = cleaned_price_str.replace(',', '.')
                    if cleaned_price_str:
                        try:
                            price = float(cleaned_price_str)
                            break
                        except ValueError:
                            continue
            if title and "amazon" in title.lower() and len(title) < 20: return None
            if title and price is not None: return {'title': title, 'price': price}
        except Exception as e:
            print(f"Error extrayendo detalles del producto: {e}")
        return None

    def _download_image(self, img_url):
        try:
            img_response = self.session.get(img_url, timeout=10, stream=True)
            img_response.raise_for_status()
            if 'image' in img_response.headers.get('Content-Type', ''):
                img_id = str(uuid.uuid4())[:8]
                path = urlparse(img_url).path
                ext = os.path.splitext(path)[1] or '.jpg'
                clean_ext = re.sub(r'[^.a-zA-Z0-9]', '', ext)
                if not clean_ext or len(clean_ext) > 5: clean_ext = '.jpg'
                img_filename = f'extracted_{img_id}{clean_ext}'
                
                # Guardamos las imágenes extraídas en una carpeta que no necesita ser persistente
                img_path_fs = os.path.join(STATIC_IMAGES_FOLDER, img_filename)
                
                with open(img_path_fs, 'wb') as f:
                    for chunk in img_response.iter_content(1024): f.write(chunk)
                
                return {'web_path': url_for('static', filename=f'images/{img_filename}'),
                        'filesystem_path': img_path_fs}
        except Exception as e:
            print(f"No se pudo descargar la imagen {img_url}: {e}")
        return None

    def _extract_images(self, soup, base_url):
        main_img_selectors = ['#landingImage', '#imgTagWrapperId img', 'meta[property="og:image"]']
        for selector in main_img_selectors:
            img_tag = soup.select_one(selector)
            if img_tag:
                src = img_tag.get('content') or img_tag.get('data-src') or img_tag.get('src')
                if src and not src.startswith('data:image'):
                    img_url = urljoin(base_url, src)
                    img_info = self._download_image(img_url)
                    if img_info: return [img_info]
        for img_tag in soup.find_all('img', limit=20):
            src = img_tag.get('data-src') or img_tag.get('src')
            if not src or src.startswith('data:image'): continue
            if any(keyword in src.lower() for keyword in
                   ['logo', 'icon', 'spinner', 'loader', 'pixel', 'badge', 'avatar', 'ad', 'banner', 'svg']): continue
            try:
                width = int(re.sub(r'\D', '', str(img_tag.get('width', '0'))))
                height = int(re.sub(r'\D', '', str(img_tag.get('height', '0'))))
                if width < 150 or height < 150: continue
            except (ValueError, TypeError):
                pass
            img_url = urljoin(base_url, src)
            img_info = self._download_image(img_url)
            if img_info: return [img_info]
        return []

    def extract_web_data(self, url):
        try:
            parsed_url = urlparse(url)
            clean_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
            response = self.session.get(clean_url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            data = {'url': url, 'title': self._extract_title(soup), 'description': self._extract_description(soup),
                    'images': self._extract_images(soup, url), 'product_details': self._extract_product_details(soup),
                    'status': 'success'}
            return data
        except requests.exceptions.RequestException as e:
            return {'error': f'Network error or invalid URL: {str(e)}', 'status': 'error'}
        except Exception as e:
            return {'error': f'Unexpected error: {str(e)}', 'status': 'error'}

    def _extract_title(self, soup):
        og_title = soup.find('meta', property='og:title')
        if og_title: return og_title.get('content', 'Untitled').strip()
        return soup.find('title').get_text(strip=True) if soup.find('title') else 'Untitled'

    def _extract_description(self, soup):
        og_desc = soup.find('meta', property='og:description')
        if og_desc: return og_desc.get('content', 'No description.').strip()
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        return meta_desc.get('content', 'No description.').strip() if meta_desc else 'No description.'


# --- Clase para Generar Cotizaciones en PDF (SIN CAMBIOS) ---
class QuoteGenerator:
    """Clase para generar cotizaciones profesionales en formato PDF."""
    
    # ... (El código de la clase QuoteGenerator no necesita cambios) ...
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._create_custom_styles()

    def _create_custom_styles(self):
        self.styles.add(
            ParagraphStyle(name='CustomTitle', parent=self.styles['h1'], fontSize=20, spaceAfter=16, alignment=TA_LEFT,
                           textColor=colors.HexColor('#2c3e50')))
        self.styles.add(ParagraphStyle(name='HeaderInfo', parent=self.styles['Normal'], fontSize=9, leading=12))
        self.styles.add(
            ParagraphStyle(name='ClientInfo', parent=self.styles['Normal'], fontSize=9, leading=12, alignment=TA_RIGHT))
        self.styles.add(ParagraphStyle(name='TotalLabel', parent=self.styles['Normal'], fontName='Helvetica-Bold',
                                       alignment=TA_RIGHT))
        self.styles.add(
            ParagraphStyle(name='GrandTotal', parent=self.styles['Normal'], fontName='Helvetica-Bold', fontSize=12,
                           alignment=TA_RIGHT))
        self.styles.add(ParagraphStyle(name='TermsHeader', parent=self.styles['h3'], fontSize=10, spaceBefore=10))

    def generate_quote_pdf(self, quote_data):
        try:
            quote_num = quote_data.get('quote_number') or str(uuid.uuid4())[:6].upper()
            filename = f'quote_{secure_filename(quote_num)}.pdf'
            
            # Usamos la ruta QUOTES_FOLDER definida globalmente
            filepath = os.path.join(QUOTES_FOLDER, filename)
            
            doc = SimpleDocTemplate(filepath, pagesize=A4, rightMargin=inch * 0.7, leftMargin=inch * 0.7,
                                    topMargin=inch * 0.7, bottomMargin=inch * 0.7)
            story = []
            logo_path = quote_data.get('company_logo_path')
            logo_img = Image(logo_path, width=1.5 * inch, height=1.5 * inch) if logo_path and os.path.exists(
                logo_path) else None
            if logo_img: logo_img.hAlign = 'LEFT'
            company_info_text = f"<b>{quote_data.get('company_name', 'Your Company Name')}</b><br/>{quote_data.get('company_address', 'Your Company Address')}<br/>Tel: {quote_data.get('company_phone', 'N/A')}<br/>Email: {quote_data.get('company_email', 'N/A')}"
            header_right_text = f"<b>QUOTE</b><br/><br/><b>DATE:</b> {datetime.now().strftime('%Y-%m-%d')}<br/><b>QUOTE #:</b> {quote_num}<br/><b>VALID UNTIL:</b> {quote_data.get('valid_until', 'N/A')}"
            header_table = Table([[logo_img if logo_img else Paragraph(company_info_text, self.styles['HeaderInfo']),
                                   Paragraph(header_right_text, self.styles['ClientInfo'])]],
                                 colWidths=[3.5 * inch, 3 * inch])
            header_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
            story.append(header_table)
            story.append(Spacer(1, 20))
            client_info = f"<b>BILL TO:</b><br/>{quote_data.get('client_name', 'Client Name')}<br/>{quote_data.get('client_contact', '')}"
            client_table = Table([[Paragraph(client_info, self.styles['HeaderInfo'])]], colWidths=[6.5 * inch],
                                 style=[('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#dddddd')),
                                        ('PADDING', (0, 0), (-1, -1), 10)])
            story.append(client_table)
            story.append(Spacer(1, 25))
            items = quote_data.get('items', [])
            if items:
                table_data = [['IMAGE', 'DESCRIPTION', 'QTY', 'UNIT PRICE', 'TOTAL']]
                subtotal = 0
                for item in items:
                    quantity = float(item.get('quantity', 1))
                    price = float(item.get('price', 0))
                    total_item = quantity * price
                    subtotal += total_item
                    description_p = Paragraph(item.get('description', ''), self.styles['Normal'])

                    item_image = ''
                    image_fs_path = item.get('image_filesystem_path')
                    if image_fs_path and os.path.exists(image_fs_path):
                        try:
                            item_image = Image(image_fs_path, width=0.6 * inch, height=0.6 * inch)
                            item_image.hAlign = 'CENTER'
                        except Exception as e:
                            print(f"Error processing image for PDF item: {e}")
                            item_image = 'N/A'

                    table_data.append(
                        [item_image, description_p, str(quantity), f"${price:,.2f}", f"${total_item:,.2f}"])

                items_table = Table(table_data, colWidths=[0.7 * inch, 3.1 * inch, 0.7 * inch, 1 * inch, 1 * inch],
                                    repeatRows=1)
                items_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12), ('TOPPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('ALIGN', (1, 1), (1, -1), 'LEFT'),
                    ('ALIGN', (2, 1), (-1, -1), 'RIGHT'), ('PADDING', (0, 0), (-1, -1), 5)]))
                story.append(items_table)
                discount = float(quote_data.get('discount', 0))
                tax_rate = float(quote_data.get('tax_rate', 7)) / 100
                subtotal_after_discount = subtotal - discount
                tax_amount = subtotal_after_discount * tax_rate
                grand_total = subtotal_after_discount + tax_amount
                totals_data = [['Subtotal:', f'${subtotal:,.2f}'], ['Discount:', f'-${discount:,.2f}'],
                               [f'Sales Tax ({tax_rate * 100:.1f}%):', f'${tax_amount:,.2f}']]
                totals_table = Table(totals_data, colWidths=[1.5 * inch, 1 * inch])
                totals_table.setStyle(TableStyle(
                    [('ALIGN', (0, 0), (-1, -1), 'RIGHT'), ('FONTSIZE', (0, 0), (-1, -1), 10),
                     ('LEFTPADDING', (0, 0), (-1, -1), 20)]))
                grand_total_data = [[Paragraph('TOTAL:', self.styles['GrandTotal']),
                                     Paragraph(f'${grand_total:,.2f}', self.styles['GrandTotal'])]]
                grand_total_table = Table(grand_total_data, colWidths=[1.5 * inch, 1 * inch])
                grand_total_table.setStyle(TableStyle(
                    [('ALIGN', (0, 0), (-1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                     ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eeeeee')), ('TOPPADDING', (0, 0), (-1, -1), 10),
                     ('BOTTOMPADDING', (0, 0), (-1, -1), 10), ('LEFTPADDING', (0, 0), (-1, -1), 20)]))
                summary_table = Table([[totals_table], [grand_total_table]], style=[('ALIGN', (0, 0), (0, 0), 'RIGHT')])
                story.append(summary_table)
            story.append(Spacer(1, 30))
            if quote_data.get('terms'):
                story.append(Paragraph("Terms and Conditions:", self.styles['TermsHeader']))
                story.append(Paragraph(quote_data['terms'].replace('\n', '<br/>'), self.styles['Normal']))
            doc.build(story)
            return filename
        except Exception:
            traceback.print_exc()
            raise


# --- Instancias globales (SIN CAMBIOS) ---
extractor = WebDataExtractor()
quote_gen = QuoteGenerator()

# --- Template HTML con JavaScript (SIN CAMBIOS) ---
HTML_TEMPLATE = r"""
... (El bloque gigante de HTML y JS no necesita cambios) ...
"""


# --- RUTAS DE FLASK (CON AJUSTES) ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/extract', methods=['POST'])
def extract_data_route():
    url = request.form.get('url')
    if not url: return jsonify({'error': 'URL not provided.', 'status': 'error'}), 400
    if not url.startswith(('http://', 'https://')): url = 'http://' + url
    data = extractor.extract_web_data(url)
    return jsonify(data)


@app.route('/generate-quote', methods=['POST'])
def generate_quote_route():
    try:
        form_data = request.form.to_dict()
        logo_path = None
        if 'company_logo' in request.files:
            logo_file = request.files['company_logo']
            if logo_file.filename != '':
                filename = secure_filename(logo_file.filename)
                # Guardamos el logo en la carpeta de subidas persistente
                logo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                logo_file.save(logo_path)
                form_data['company_logo_path'] = logo_path
        
        if 'items' in form_data:
            form_data['items'] = json.loads(form_data['items'])

        pdf_filename = quote_gen.generate_quote_pdf(form_data)
        
        # CAMBIO IMPORTANTE: La URL para ver el PDF ahora apunta a nuestra nueva ruta /quotes/
        file_url = url_for('get_quote_pdf', filename=pdf_filename, _external=True)
        
        return jsonify({'status': 'success', 'file_url': file_url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)}), 500

# NUEVA RUTA: Sirve los PDFs desde el disco persistente
@app.route('/quotes/<filename>')
def get_quote_pdf(filename):
    # Sirve los archivos desde la carpeta QUOTES_FOLDER en el disco persistente
    return send_from_directory(QUOTES_FOLDER, filename)


# CAMBIO FINAL: Eliminar la sección if __name__ == '__main__':
# Gunicorn se encargará de ejecutar la aplicación, por lo que este bloque ya no es necesario para producción.
# Puedes dejarlo si quieres seguir probando localmente con "python app.py"
if __name__ == '__main__':
    # Para pruebas locales, el host debe ser 0.0.0.0 para que sea accesible
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)