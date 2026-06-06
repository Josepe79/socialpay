from pydantic import BaseModel
from typing import List, Optional, Union


class BeneficiarySchema(BaseModel):
    token_anonimo: str
    saldo_disponible: float
    codigo_proyecto_fse: Optional[str] = None
    gestor_uuid: Optional[str] = None


class SystemUserCreateSchema(BaseModel):
    email: str
    password: str
    rol: str


class SystemUserUpdateSchema(BaseModel):
    email: str
    password: Optional[str] = None
    rol: str


class AsignarFondosSchema(BaseModel):
    gestor_id: str
    codigo_proyecto_fse: str
    presupuesto_total: Union[float, str]
    tasa_cofinanciacion: Union[float, str]


class GestorCreateSchema(BaseModel):
    nombre_institucion: str
    cif: str
    direccion: str
    codigo_proyecto_fse: str
    presupuesto_inicial: Union[float, str]
    tasa_cofinanciacion: Union[float, str]
    responsable: str
    email: str
    password: str
    movil_mfa: str


class SupermarketProductSchema(BaseModel):
    supermercado_id: str
    codigo_barras: str
    nombre: str
    precio: float
    categoria_fse: Optional[str] = None
    palabras_clave_ocr: Optional[List[str]] = []
