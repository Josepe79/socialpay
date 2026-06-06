import requests

# Mapeo de etiquetas OFF a categorías FSE+ internas
_OFF_CATEGORY_MAP = {
    # Lácteos
    'en:dairy-products': 'dairy', 'en:milks': 'dairy', 'en:cheeses': 'dairy',
    'en:yogurts': 'dairy', 'en:butters': 'dairy', 'en:creams': 'dairy',
    # Bebidas no alcohólicas
    'en:beverages': 'beverages', 'en:waters': 'beverages',
    'en:fruit-juices': 'beverages', 'en:sodas': 'beverages',
    'en:soft-drinks': 'beverages',
    # Bebidas alcohólicas (bloqueadas)
    'en:alcoholic-beverages': 'alcoholic-beverages', 'en:beers': 'alcoholic-beverages',
    'en:wines': 'alcoholic-beverages', 'en:spirits': 'alcoholic-beverages',
    'en:ciders': 'alcoholic-beverages',
    # Carne y embutidos
    'en:meats': 'meat', 'en:poultry': 'meat', 'en:hams': 'meat',
    'en:sausages': 'meat', 'en:deli-meats': 'meat',
    # Pescado
    'en:fish-and-seafood': 'fish', 'en:canned-fish': 'fish',
    'en:tunas': 'fish', 'en:salmons': 'fish',
    # Huevos
    'en:eggs': 'eggs',
    # Congelados
    'en:frozen-foods': 'frozen', 'en:ice-creams': 'frozen', 'en:pizzas': 'frozen',
    # Snacks y galletas
    'en:snacks': 'snack', 'en:chips-and-crisps': 'snack',
    'en:biscuits-and-cakes': 'snack', 'en:crackers': 'snack',
    # Chocolate y cacao
    'en:chocolates': 'chocolate', 'en:chocolate-candies': 'chocolate',
    'en:cocoa-and-its-products': 'cocoa',
    # Café e infusiones
    'en:coffees': 'coffee', 'en:teas': 'tea', 'en:infusions': 'tea',
    # Cereales y pasta
    'en:cereals-and-their-products': 'cereals', 'en:breakfast-cereals': 'cereals',
    'en:pasta': 'pasta', 'en:rice': 'grains', 'en:flours': 'bread',
    # Pan y bollería
    'en:breads': 'bakery', 'en:pastries': 'bakery', 'en:cakes': 'bakery',
    # Aceites y condimentos
    'en:plant-based-oils': 'oils', 'en:olive-oils': 'oils',
    'en:vinegars': 'condiments', 'en:salts': 'condiments',
    # Salsas y conservas
    'en:sauces': 'sauce', 'en:ketchup': 'sauce',
    'en:mayonnaises': 'sauce', 'en:tomato-sauces': 'sauce',
    # Azúcares
    'en:sweeteners': 'sweeteners', 'en:sugars': 'sweeteners',
    # Higiene
    'en:hygiene-products': 'hygiene', 'en:shampoos': 'hygiene',
    'en:soaps': 'hygiene', 'en:toothpastes': 'hygiene',
    'en:deodorants': 'hygiene', 'en:toilet-papers': 'hygiene',
    # Bebé
    'en:baby-foods': 'baby', 'en:diapers': 'baby', 'en:infant-formulas': 'baby',
    # Limpieza
    'en:cleaning-products': 'cleaning', 'en:detergents': 'cleaning',
    'en:bleaches': 'cleaning', 'en:fabric-softeners': 'cleaning',
}

# Etiquetas OFF que implican producto no permitido en FSE+
_BLOCKED_OFF_TAGS = {
    'en:alcoholic-beverages', 'en:beers', 'en:wines', 'en:spirits',
    'en:ciders', 'en:tobacco', 'en:tobaccos', 'en:cigarettes',
}


class ProductMatcher:
    def __init__(self):
        self.off_url = "https://world.openfoodfacts.org/api/v0/product/"
        self._cache = {}

    def _map_category(self, categories_tags: list) -> str:
        for tag in categories_tags:
            cat = _OFF_CATEGORY_MAP.get(tag)
            if cat:
                return cat
        return 'unknown'

    def get_product_info(self, barcode: str) -> dict:
        """Consulta Open Food Facts. Devuelve {name, allowed, category}."""
        if barcode in self._cache:
            return self._cache[barcode]

        try:
            headers = {"User-Agent": "SocialPayMVP - Android - Version 1.0 - www.jepco.es"}
            response = requests.get(
                f"{self.off_url}{barcode}.json", headers=headers, timeout=5
            )

            if response.status_code == 429:
                return {"name": "Límite de consultas alcanzado. Espera unos segundos.",
                        "allowed": True, "category": "unknown"}

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 1:
                    product = data['product']
                    name = product.get('product_name', 'Producto desconocido')
                    categories = product.get('categories_tags', [])
                    is_allowed = not any(tag in _BLOCKED_OFF_TAGS for tag in categories)
                    category = self._map_category(categories)
                    result = {"name": name, "allowed": is_allowed, "category": category}
                else:
                    result = {"name": f"Producto no encontrado en base de datos.",
                              "allowed": True, "category": "unknown"}
            else:
                result = {"name": f"Error HTTP {response.status_code} desde OFF",
                          "allowed": True, "category": "unknown"}

            self._cache[barcode] = result
            return result

        except Exception as e:
            return {"name": f"Error interno: {str(e)}", "allowed": True, "category": "unknown"}
