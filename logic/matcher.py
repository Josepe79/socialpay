import requests
import re

class ProductMatcher:
    def __init__(self):
        # URL base de la API de Open Food Facts
        self.off_url = "https://world.openfoodfacts.org/api/v0/product/"
        # Caché en memoria para no repetir llamadas a OFF (evita 429)
        self._cache = {}

    def get_product_info(self, barcode):
        """Consulta Open Food Facts para identificar el producto."""
        # Devolvemos desde caché si ya lo consultamos antes
        if barcode in self._cache:
            return self._cache[barcode]

        try:
            headers = {"User-Agent": "SocialPayMVP - Android - Version 1.0 - www.jepco.es"}
            response = requests.get(f"{self.off_url}{barcode}.json", headers=headers, timeout=5)

            if response.status_code == 429:
                result = {"name": "Límite de consultas alcanzado. Espera unos segundos.", "allowed": True}
                return result  # No guardamos en caché para poder reintentar

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 1:
                    product = data['product']
                    name = product.get('product_name', 'Producto desconocido')
                    categories = product.get('categories_tags', [])

                    # Lógica de bloqueo: No permitimos alcohol ni tabaco
                    is_allowed = not any(tag in categories for tag in ['en:alcoholic-beverages', 'en:tobacco'])

                    result = {"name": name, "allowed": is_allowed}
                else:
                    result = {"name": f"Producto no en base de datos (Status: {data.get('status')})", "allowed": True}
            else:
                result = {"name": f"Error HTTP {response.status_code} desde OFF", "allowed": True}

            # Guardamos en caché el resultado
            self._cache[barcode] = result
            return result

        except Exception as e:
            return {"name": f"Error interno: {str(e)}", "allowed": True}

    def match_ticket_vs_cart(self, cart_total, ocr_text):
        """
        Compara el total del carrito escaneado con el texto del ticket.
        Busca el patrón de moneda (ej: 42.50) en el texto del OCR.
        """
        # Buscamos números que parezcan importes (ej: 42,50 o 42.50)
        amounts = re.findall(r'\d+[.,]\d{2}', ocr_text)
        
        # Limpiamos los puntos y comas para comparar decimales
        clean_amounts = [float(a.replace(',', '.')) for a in amounts]
        
        # Si el total del carrito está entre los números del ticket, ¡HAY MATCH!
        if cart_total in clean_amounts:
            return True
            
        return False
