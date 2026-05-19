import difflib

class TicketValidator:
    def __init__(self, name_tolerance=0.6, price_tolerance=0.05, total_tolerance=0.10):
        self.name_tolerance = name_tolerance
        self.price_tolerance = price_tolerance
        self.total_tolerance = total_tolerance

    def fuzzy_match(self, name_a: str, name_b: str) -> float:
        """Returns a similarity score between 0 and 1 for two strings."""
        if not name_a or not name_b:
            return 0.0
        return difflib.SequenceMatcher(None, name_a.lower(), name_b.lower()).ratio()

    def validate(self, cart_items: list, cart_total: float, ticket_data: dict) -> dict:
        """
        Validates the cart against the OCR extracted ticket data.
        
        ticket_data format:
        {
            "total": float,
            "items": [{"name": str, "price": float}, ...]
        }
        """
        if not ticket_data or "items" not in ticket_data or "total" not in ticket_data:
            return {
                "status": "unreadable",
                "message": "No se pudo extraer la información del ticket.",
                "total_ok": False,
                "score": 0,
                "items": []
            }

        ticket_total = ticket_data.get("total", 0.0)
        total_diff = abs(cart_total - ticket_total)
        total_ok = total_diff <= self.total_tolerance

        validation_items = []
        ticket_items = ticket_data.get("items", [])[:] # Copy to manipulate
        
        matches = 0
        
        # 1. Match cart items against ticket items
        for cart_item in cart_items:
            cart_name = cart_item.get("name", "")
            cart_price = float(cart_item.get("price", 0.0))
            
            best_match_idx = -1
            best_score = 0
            
            # Find the best matching item in the ticket
            for idx, t_item in enumerate(ticket_items):
                score = self.fuzzy_match(cart_name, t_item.get("name", ""))
                if score > best_score:
                    best_score = score
                    best_match_idx = idx
            
            if best_match_idx != -1 and best_score >= self.name_tolerance:
                t_item = ticket_items.pop(best_match_idx)
                t_price = float(t_item.get("price", 0.0))
                price_diff = abs(cart_price - t_price)
                
                if price_diff <= self.price_tolerance:
                    match_status = "ok"
                    matches += 1
                else:
                    match_status = "price_diff"
                
                validation_items.append({
                    "cart_name": cart_name,
                    "ticket_name": t_item.get("name", ""),
                    "cart_price": cart_price,
                    "ticket_price": t_price,
                    "match": match_status
                })
            else:
                validation_items.append({
                    "cart_name": cart_name,
                    "ticket_name": "---",
                    "cart_price": cart_price,
                    "ticket_price": None,
                    "match": "not_found"
                })
        
        # 2. Add extra items found in ticket but not in cart
        for t_item in ticket_items:
            validation_items.append({
                "cart_name": "---",
                "ticket_name": t_item.get("name", ""),
                "cart_price": None,
                "ticket_price": float(t_item.get("price", 0.0)),
                "match": "extra_in_ticket"
            })
            
        score_pct = (matches / len(cart_items)) * 100 if cart_items else 0
        
        status = "validated" if total_ok and score_pct >= 80 else "discrepancy"

        return {
            "status": status,
            "total_ok": total_ok,
            "total_diff": total_diff,
            "ticket_total": ticket_total,
            "cart_total": cart_total,
            "score": score_pct,
            "items": validation_items
        }
