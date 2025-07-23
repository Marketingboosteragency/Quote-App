#!/usr/bin/env python3
"""
WebApp de Cotización y Extracción de Datos con IA (Versión Final para Render Free Tier)
Aplicación Flask que:
1. Genera plantillas de cotización y las envía directamente para descarga (sin guardar en disco).
2. Extrae datos de productos e imágenes de enlaces web de forma robusta usando un servicio de scraping.
"""

from flask import Flask, render_template_string, request, jsonify, url_for, send_file
import io
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
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_super_secreta_y_dificil_de_adivinar')

# --- CONFIGURACIÓN PARA SISTEMAS DE FICHEROS EFÍMEROS (RENDER FREE TIER) ---
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
STATIC_IMAGES_FOLDER = os.path.join(STATIC_FOLDER, 'images')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_IMAGES_FOLDER, exist_ok=True) 

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# --- Clase para Extracción de Datos Web (ACTUALIZADA PARA USAR SCRAPERAPI) ---
class WebDataExtractor:
    """Clase mejorada para extraer datos de páginas web usando ScraperAPI para evitar bloqueos."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        })

    def _extract_product_details(self, soup):
        try:
            title_selectors = ['h1[itemprop="name"]', '#productTitle', '.ui-pdp-title', 'h1']
            title = next((soup.select_one(s).get_text(strip=True) for s in title_selectors if soup.select_one(s)), None)

            price_selectors = ['span[itemprop="price"]', 'span.a-offscreen', '.andes-money-amount__fraction', '.a-price-whole']
            price = None
            for selector in price_selectors:
                el = soup.select_one(selector)
                if el:
                    price_str = el.get('content') or el.get_text(strip=True)
                    cleaned_price_str = re.sub(r'[^\d.]', '', price_str)
                    if cleaned_price_str:
                        try:
                            price = float(cleaned_price_str)
                            break
                        except ValueError: continue
            
            if title and "robot" in title.lower(): return None
            if title and price is not None: return {'title': title, 'price': price}
        except Exception as e:
            print(f"Error extrayendo detalles del producto: {e}")
        return None

    def _download_image(self, img_url):
        try:
            img_response = self.session.get(img_url, timeout=15, stream=True)
            img_response.raise_for_status()
            if 'image' in img_response.headers.get('Content-Type', ''):
                img_id = str(uuid.uuid4())[:8]
                ext = os.path.splitext(urlparse(img_url).path)[1] or '.jpg'
                clean_ext = re.sub(r'[^.a-zA-Z0-9]', '', ext)
                if not clean_ext or len(clean_ext) > 5: clean_ext = '.jpg'
                img_filename = f'extracted_{img_id}{clean_ext}'
                img_path_fs = os.path.join(STATIC_IMAGES_FOLDER, img_filename)
                with open(img_path_fs, 'wb') as f:
                    for chunk in img_response.iter_content(1024): f.write(chunk)
                return {'web_path': url_for('static', filename=f'images/{img_filename}'), 'filesystem_path': img_path_fs}
        except Exception as e:
            print(f"No se pudo descargar la imagen {img_url}: {e}")
        return None

    def _extract_images(self, soup, base_url):
        main_img_selectors = ['div.w_V_x_ img', '#landingImage', '#imgTagWrapperId img', 'meta[property="og:image"]']
        for selector in main_img_selectors:
            img_tag = soup.select_one(selector)
            if img_tag:
                src = img_tag.get('content') or img_tag.get('data-src') or img_tag.get('src')
                if src and not src.startswith('data:image'):
                    img_url = urljoin(base_url, src)
                    img_info = self._download_image(img_url)
                    if img_info: return [img_info]
        return []

    def extract_web_data(self, url):
        API_KEY = os.environ.get('SCRAPER_API_KEY')
        if not API_KEY:
            return {'error': 'ScraperAPI key not configured on the server.', 'status': 'error'}

        scraper_url = 'http://api.scraperapi.com'
        payload = {'api_key': API_KEY, 'url': url}

        try:
            response = requests.get(scraper_url, params=payload, timeout=60)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'lxml')

            data = {
                'url': url, 
                'title': self._extract_title(soup), 
                'description': self._extract_description(soup),
                'images': self._extract_images(soup, url), 
                'product_details': self._extract_product_details(soup),
                'status': 'success'
            }
            return data
        except requests.exceptions.RequestException as e:
            return {'error': f'Network error or failed request to ScraperAPI: {str(e)}', 'status': 'error'}
        except Exception as e:
            return {'error': f'Unexpected error during scraping: {str(e)}', 'status': 'error'}
    
    def _extract_title(self, soup):
        og_title = soup.find('meta', property='og:title')
        if og_title: return og_title.get('content', 'Untitled').strip()
        return soup.find('title').get_text(strip=True) if soup.find('title') else 'Untitled'

    def _extract_description(self, soup):
        og_desc = soup.find('meta', property='og:description')
        if og_desc: return og_desc.get('content', 'No description.').strip()
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        return meta_desc.get('content', 'No description.').strip() if meta_desc else 'No description.'


# --- Clase para Generar Cotizaciones en PDF ---
class QuoteGenerator:
    """Clase para generar cotizaciones profesionales en formato PDF."""
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._create_custom_styles()
    def _create_custom_styles(self):
        self.styles.add(ParagraphStyle(name='CustomTitle', parent=self.styles['h1'], fontSize=20, spaceAfter=16, alignment=TA_LEFT, textColor=colors.HexColor('#2c3e50')))
        self.styles.add(ParagraphStyle(name='HeaderInfo', parent=self.styles['Normal'], fontSize=9, leading=12))
        self.styles.add(ParagraphStyle(name='ClientInfo', parent=self.styles['Normal'], fontSize=9, leading=12, alignment=TA_RIGHT))
        self.styles.add(ParagraphStyle(name='TotalLabel', parent=self.styles['Normal'], fontName='Helvetica-Bold', alignment=TA_RIGHT))
        self.styles.add(ParagraphStyle(name='GrandTotal', parent=self.styles['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=TA_RIGHT))
        self.styles.add(ParagraphStyle(name='TermsHeader', parent=self.styles['h3'], fontSize=10, spaceBefore=10))
    def generate_quote_pdf_in_memory(self, quote_data):
        try:
            buffer = io.BytesIO()
            quote_num = quote_data.get('quote_number') or str(uuid.uuid4())[:6].upper()
            doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=inch * 0.7, leftMargin=inch * 0.7, topMargin=inch * 0.7, bottomMargin=inch * 0.7)
            story = []
            logo_path = quote_data.get('company_logo_path')
            logo_img = Image(logo_path, width=1.5 * inch, height=1.5 * inch) if logo_path and os.path.exists(logo_path) else None
            if logo_img: logo_img.hAlign = 'LEFT'
            company_info_text = f"<b>{quote_data.get('company_name', 'Your Company Name')}</b><br/>{quote_data.get('company_address', 'Your Company Address')}<br/>Tel: {quote_data.get('company_phone', 'N/A')}<br/>Email: {quote_data.get('company_email', 'N/A')}"
            header_right_text = f"<b>QUOTE</b><br/><br/><b>DATE:</b> {datetime.now().strftime('%Y-%m-%d')}<br/><b>QUOTE #:</b> {quote_num}<br/><b>VALID UNTIL:</b> {quote_data.get('valid_until', 'N/A')}"
            header_table_content = [logo_img if logo_img else Paragraph(company_info_text, self.styles['HeaderInfo']), Paragraph(header_right_text, self.styles['ClientInfo'])]
            header_table = Table([header_table_content], colWidths=[3.5 * inch, 3 * inch])
            header_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
            story.append(header_table)
            story.append(Spacer(1, 20))
            client_info = f"<b>BILL TO:</b><br/>{quote_data.get('client_name', 'Client Name')}<br/>{quote_data.get('client_contact', '')}"
            client_table = Table([[Paragraph(client_info, self.styles['HeaderInfo'])]], colWidths=[6.5 * inch], style=[('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#dddddd')), ('PADDING', (0, 0), (-1, -1), 10)])
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
                    table_data.append([item_image, description_p, str(quantity), f"${price:,.2f}", f"${total_item:,.2f}"])
                items_table = Table(table_data, colWidths=[0.7 * inch, 3.1 * inch, 0.7 * inch, 1 * inch, 1 * inch], repeatRows=1)
                # --- CORRECCIÓN DEL TYPO AQUÍ ---
                items_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), # CAMBIADO de whitespoke a whitesmoke
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('TOPPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('ALIGN', (1, 1), (1, -1), 'LEFT'),
                    ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
                    ('PADDING', (0, 0), (-1, -1), 5)
                ]))
                story.append(items_table)
                discount = float(quote_data.get('discount', 0))
                tax_rate = float(quote_data.get('tax_rate', 7)) / 100
                subtotal_after_discount = subtotal - discount
                tax_amount = subtotal_after_discount * tax_rate
                grand_total = subtotal_after_discount + tax_amount
                totals_data = [['Subtotal:', f'${subtotal:,.2f}'], ['Discount:', f'-${discount:,.2f}'], [f'Sales Tax ({tax_rate * 100:.1f}%):', f'${tax_amount:,.2f}']]
                totals_table = Table(totals_data, colWidths=[1.5 * inch, 1 * inch])
                totals_table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'RIGHT'), ('FONTSIZE', (0, 0), (-1, -1), 10), ('LEFTPADDING', (0, 0), (-1, -1), 20)]))
                grand_total_data = [[Paragraph('TOTAL:', self.styles['GrandTotal']), Paragraph(f'${grand_total:,.2f}', self.styles['GrandTotal'])]]
                grand_total_table = Table(grand_total_data, colWidths=[1.5 * inch, 1 * inch])
                grand_total_table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eeeeee')), ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10), ('LEFTPADDING', (0, 0), (-1, -1), 20)]))
                summary_table = Table([[totals_table], [grand_total_table]], style=[('ALIGN', (0, 0), (0, 0), 'RIGHT')])
                story.append(summary_table)
            story.append(Spacer(1, 30))
            if quote_data.get('terms'):
                story.append(Paragraph("Terms and Conditions:", self.styles['TermsHeader']))
                story.append(Paragraph(quote_data['terms'].replace('\n', '<br/>'), self.styles['Normal']))
            doc.build(story)
            buffer.seek(0)
            return buffer
        except Exception:
            traceback.print_exc()
            raise

# --- Instancias globales ---
extractor = WebDataExtractor()
quote_gen = QuoteGenerator()

# --- Template HTML con JavaScript ---
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
    <title>Quote Generator & Data Extractor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f0f2f5; color: #333; line-height: 1.6; }
        .container { display: flex; max-width: 1600px; margin: 20px auto; background: #f0f2f5; gap: 20px; flex-wrap: wrap; }
        .column { background-color: white; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); padding: 25px; }
        .left-column { flex: 1; min-width: 350px; }
        .right-column { flex: 2; min-width: 500px; }
        h1 { font-size: 24px; color: #2c3e50; margin-bottom: 20px; border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }
        h2 { font-size: 20px; color: #34495e; margin-bottom: 15px; margin-top: 25px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; font-weight: 600; margin-bottom: 5px; font-size: 14px; }
        input[type="text"], input[type="url"], input[type="email"], input[type="tel"], input[type="date"], input[type="number"], textarea { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; transition: border-color 0.3s; }
        input:focus, textarea:focus { border-color: #3498db; outline: none; }
        input:required:invalid { border-color: #e74c3c; }
        textarea { resize: vertical; min-height: 80px; }
        .btn { display: inline-block; padding: 10px 20px; border: none; border-radius: 4px; font-size: 16px; font-weight: bold; color: white; cursor: pointer; text-align: center; transition: background-color 0.3s; }
        .btn-primary { background-color: #3498db; } .btn-primary:hover { background-color: #2980b9; }
        .btn-secondary { background-color: #2ecc71; } .btn-secondary:hover { background-color: #27ae60; }
        .btn-danger { background-color: #e74c3c; padding: 4px 8px; font-size: 12px; } .btn-danger:hover { background-color: #c0392b; }
        #loader, #pdf-loader { display: none; text-align: center; padding: 20px; }
        .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        #extracted-data { margin-top: 20px; }
        .extracted-product { border: 1px solid #e0e0e0; padding: 15px; border-radius: 5px; margin-top: 10px; background: #fafafa; }
        .images-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; margin-top: 10px; }
        .images-grid img { max-width: 100%; height: auto; border-radius: 4px; object-fit: cover; border: 1px solid #ddd; padding: 5px;}
        #quote-items-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        #quote-items-table th, #quote-items-table td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 14px; vertical-align: middle; }
        #quote-items-table th { background-color: #f2f2f2; font-weight: 600; }
        #quote-items-table input { padding: 5px; font-size: 14px; max-width: 100px; }
        #quote-items-table input.desc-input { max-width: none; }
        #results { margin-top: 20px; padding: 15px; border-radius: 5px; }
        .success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .company-details { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="column left-column">
            <h1>1. Extract Data from URL</h1>
            <form id="extract-form">
                <div class="form-group"><label for="url">Product URL</label><input type="url" id="url" name="url" placeholder="https://www.amazon.com/..." required></div>
                <button type="submit" class="btn btn-primary">Extract Data</button>
            </form>
            <div id="loader"><div class="spinner"></div><p>Extracting data...</p></div>
            <div id="extraction-results" class="results" style="display:none;"></div>
            <div id="extracted-data"></div>
        </div>
        <div class="column right-column">
            <h1>2. Generate Quote</h1>
            <form id="quote-form">
                <h2>Your Company Details</h2>
                <div class="company-details">
                    <div class="form-group"><label for="company_name">Company Name</label><input type="text" id="company_name" name="company_name" required></div>
                    <div class="form-group"><label for="company_phone">Contact Phone</label><input type="tel" id="company_phone" name="company_phone" required></div>
                    <div class="form-group"><label for="company_address">Address</label><input type="text" id="company_address" name="company_address" required></div>
                    <div class="form-group"><label for="company_email">Email</label><input type="email" id="company_email" name="company_email" required></div>
                    <div class="form-group"><label for="company_logo">Company Logo</label><input type="file" id="company_logo" name="company_logo" accept="image/png, image/jpeg"></div>
                </div>
                <h2>Client & Quote Details</h2>
                <div class="company-details">
                    <div class="form-group"><label for="client_name">Client Name</label><input type="text" id="client_name" name="client_name" required></div>
                    <div class="form-group"><label for="client_contact">Client Contact / Address</label><input type="text" id="client_contact" name="client_contact" required></div>
                    <div class="form-group"><label for="valid_until">Valid Until</label><input type="date" id="valid_until" name="valid_until" required></div>
                </div>
                <h2>Quote Items</h2>
                <table id="quote-items-table">
                    <thead><tr><th>Description</th><th>Qty</th><th>Unit Price</th><th>Total</th><th>Action</th></tr></thead>
                    <tbody id="quote-items-body"></tbody>
                </table>
                <button type="button" id="add-item-btn" class="btn btn-secondary" style="margin-top:10px;">+ Add Item Manually</button>
                <h2>Totals & Terms</h2>
                <div class="company-details">
                    <div class="form-group"><label for="discount">Discount ($)</label><input type="number" id="discount" name="discount" value="0" min="0" step="0.01"></div>
                    <div class="form-group"><label for="tax_rate">Sales Tax (%)</label><input type="number" id="tax_rate" name="tax_rate" value="7" min="0" step="0.1"></div>
                </div>
                <div class="form-group"><label for="terms">Terms and Conditions</label><textarea id="terms" name="terms">Payment is due within 30 days. Warranty valid for 1 year.</textarea></div>
                <button type="submit" class="btn btn-primary">Generate Quote PDF</button>
            </form>
            <div id="pdf-loader"><div class="spinner"></div><p>Generating PDF...</p></div>
            <div id="pdf-results" class="results" style="display:none;"></div>
        </div>
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const extractForm = document.getElementById('extract-form');
            const quoteForm = document.getElementById('quote-form');
            const loader = document.getElementById('loader');
            const pdfLoader = document.getElementById('pdf-loader');
            const extractedDataContainer = document.getElementById('extracted-data');
            const extractionResults = document.getElementById('extraction-results');
            const pdfResults = document.getElementById('pdf-results');
            const addItemBtn = document.getElementById('add-item-btn');
            const quoteItemsBody = document.getElementById('quote-items-body');

            extractForm.addEventListener('submit', async function(e) {
                e.preventDefault();
                loader.style.display = 'block';
                extractedDataContainer.innerHTML = '';
                extractionResults.style.display = 'none';
                const formData = new FormData(extractForm);
                const response = await fetch('/extract', { method: 'POST', body: formData });
                loader.style.display = 'none';
                const result = await response.json();
                if (result.status === 'success') { displayExtractedData(result); } 
                else { showResult(extractionResults, 'Error: ' + result.error, true); }
            });

            function displayExtractedData(data) {
                let html = `<h2>Data Extracted</h2>`;
                if (data.product_details) {
                    const cost = data.product_details.price;
                    const imagePath = data.images && data.images.length > 0 ? data.images[0].filesystem_path : '';
                    html += `<div class="extracted-product"><p><strong>Product:</strong> ${data.product_details.title}</p><p><strong>Detected Cost:</strong> $${cost.toFixed(2)}</p></div>`;
                    addProductToQuote(data.product_details.title, cost, imagePath);
                    showResult(extractionResults, 'Product added to quote automatically.', false);
                } else {
                    html += `<p><strong>Title:</strong> ${data.title}</p><p><strong>Description:</strong> ${data.description}</p>`;
                    showResult(extractionResults, 'Could not detect a specific product. General info extracted.', true);
                }
                html += '<h3>Main Image:</h3>';
                if (data.images && data.images.length > 0) {
                    html += `<div class="images-grid"><div><img src="${data.images[0].web_path}" alt="Product image" onerror="this.onerror=null; this.src='https://via.placeholder.com/200x200.png?text=Image+Not+Found';"></div></div>`;
                } else {
                    html += `<p>No suitable image found.</p>`;
                }
                extractedDataContainer.innerHTML = html;
            }

            addItemBtn.addEventListener('click', () => addNewQuoteItem());

            function addNewQuoteItem(description = '', price = 0, quantity = 1, imageFilesystemPath = '') {
                const rowId = `item-${Date.now()}`;
                const row = document.createElement('tr');
                row.id = rowId;
                const total = price * quantity;
                const cleanDescription = description.replace(/"/g, '"');
                row.innerHTML = `
                    <td>
                        <input type="text" class="desc-input" value="${cleanDescription}" placeholder="Product/Service description">
                        <input type="hidden" class="image-path" value="${imageFilesystemPath}">
                    </td>
                    <td><input type="number" class="quantity" value="${quantity}" min="1" oninput="updateItemTotal('${rowId}')"></td>
                    <td><input type="number" class="price" value="${price.toFixed(2)}" min="0" step="0.01" oninput="updateItemTotal('${rowId}')"></td>
                    <td class="total">$${(quantity * price).toFixed(2)}</td>
                    <td><button type="button" class="btn btn-danger" onclick="this.closest('tr').remove()">X</button></td>`;
                
                const firstRow = quoteItemsBody.querySelector('tr');
                if (firstRow && firstRow.querySelector('.desc-input').value === '') {
                    quoteItemsBody.innerHTML = '';
                }
                quoteItemsBody.appendChild(row);
            }

            window.updateItemTotal = function(rowId) {
                const row = document.getElementById(rowId);
                const quantity = parseFloat(row.querySelector('.quantity').value) || 0;
                const price = parseFloat(row.querySelector('.price').value) || 0;
                row.querySelector('.total').textContent = `$${(quantity * price).toFixed(2)}`;
            }

            function addProductToQuote(title, cost, imagePath) {
                const sellingPrice = cost / 0.65;
                addNewQuoteItem(title, sellingPrice, 1, imagePath);
            };

            quoteForm.addEventListener('submit', async function(e) {
                e.preventDefault();
                if (!quoteForm.checkValidity()) {
                    quoteForm.reportValidity();
                    return;
                }
                pdfLoader.style.display = 'block';
                pdfResults.style.display = 'none';
                const quoteData = new FormData(quoteForm);
                const items = [];
                quoteItemsBody.querySelectorAll('tr').forEach(row => {
                    const description = row.querySelector('.desc-input').value;
                    if(description) {
                         items.push({
                            description: description,
                            quantity: row.querySelector('.quantity').value,
                            price: row.querySelector('.price').value,
                            image_filesystem_path: row.querySelector('.image-path').value
                        });
                    }
                });
                quoteData.append('items', JSON.stringify(items));

                try {
                    const response = await fetch('/generate-quote', { method: 'POST', body: quoteData });
                    pdfLoader.style.display = 'none';
                    if (response.ok) {
                        const blob = await response.blob();
                        const downloadUrl = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.style.display = 'none';
                        a.href = downloadUrl;
                        const disposition = response.headers.get('Content-Disposition');
                        let filename = 'cotizacion.pdf';
                        if (disposition && disposition.indexOf('attachment') !== -1) {
                            const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                            const matches = filenameRegex.exec(disposition);
                            if (matches != null && matches[1]) { 
                                filename = matches[1].replace(/['"]/g, '');
                            }
                        }
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(downloadUrl);
                        a.remove();
                        showResult(pdfResults, 'PDF generado y descarga iniciada.', false);
                    } else {
                        const errorResult = await response.json();
                        showResult(pdfResults, 'Error: ' + errorResult.error, true);
                    }
                } catch(err) {
                    pdfLoader.style.display = 'none';
                    showResult(pdfResults, 'Connection error: ' + err.message, true);
                }
            });

            function showResult(element, message, isError) {
                element.innerHTML = message;
                element.className = isError ? 'results error' : 'results success';
                element.style.display = 'block';
            }
            
            addNewQuoteItem(); 
        });
    </script>
</body>
</html>
"""

# --- RUTAS DE FLASK ---
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

        # --- VALIDACIÓN EN EL BACKEND ---
        required_fields = [
            'company_name', 'company_phone', 'company_address', 'company_email', 
            'client_name', 'client_contact', 'valid_until'
        ]
        missing_fields = [field for field in required_fields if not form_data.get(field)]
        if missing_fields:
            return jsonify({
                'status': 'error', 
                'error': f'Por favor, complete los siguientes campos obligatorios: {", ".join(missing_fields)}'
            }), 400
        # --- FIN DE LA VALIDACIÓN ---

        logo_path = None
        if 'company_logo' in request.files:
            logo_file = request.files['company_logo']
            if logo_file.filename != '':
                filename = secure_filename(logo_file.filename)
                logo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                logo_file.save(logo_path)
                form_data['company_logo_path'] = logo_path
        
        if 'items' in form_data and form_data['items']:
            form_data['items'] = json.loads(form_data['items'])
        else:
            form_data['items'] = []

        pdf_buffer = quote_gen.generate_quote_pdf_in_memory(form_data)
        
        client_name = form_data.get('client_name', 'quote')
        pdf_filename = f'Cotizacion_{secure_filename(client_name)}_{datetime.now().strftime("%Y%m%d")}.pdf'
        
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=pdf_filename,
            mimetype='application/pdf'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)}), 500

# Esta sección es para ejecutar la app localmente.
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=True)
