import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, Integer, JSON, CHAR, Boolean, CheckConstraint
from sqlalchemy.types import TypeDecorator
from app.database import Base

class GUID(TypeDecorator):
    """Tipo GUID independiente de la plataforma.
    Usa el tipo UUID nativo de PostgreSQL cuando está disponible;
    de lo contrario, recurre a CHAR(36) y gestiona la conversión de objetos UUID a string.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return value
        else:
            if isinstance(value, uuid.UUID):
                return str(value)
            else:
                return str(uuid.UUID(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value)
            return value

class Usuario(Base):
    """
    Modelo de Usuario (Beneficiario, Supermercado, Gestor, Emisor).
    
    🔒 MEDIDAS DE SEGURIDAD Y RGPD (Reglamento General de Protección de Datos de la UE):
    1. Seudonimización (Art. 5(1)(c) y Art. 32 RGPD): 
       No almacenamos datos identificativos directos (nombres, apellidos, DNI, emails) de los beneficiarios.
       El beneficiario es identificado en el sistema a través de un 'token_anonimo' aleatorio y opaco.
    2. Segregación de Información: 
       La asociación entre el 'token_anonimo' y la identidad física del beneficiario se mantiene 
       exclusivamente en los sistemas de la entidad gestora externa (ayuntamiento/trabajador social),
       aislando por completo los datos transaccionales de alimentación del backend FastAPI.
    """
    __tablename__ = 'usuarios'
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    token_anonimo = Column(String, unique=True, index=True, nullable=False)
    saldo_disponible = Column(Numeric(10, 2), default=0.00, nullable=False)
    rol = Column(String, nullable=False, default='beneficiario') # 'admin', 'upspain', 'gestor', 'supermercado', 'beneficiario'
    
    # Columnas exclusivas para roles con credenciales de acceso (ej: admin, gestor, supermercado)
    email = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=True)
    mfa_secret = Column(String, nullable=True) # Clave TOTP en Base32
    mfa_enabled = Column(Boolean, default=False, nullable=False) # True si el QR ha sido vinculado y verificado
    
    # Campos para relaciones presupuestarias y compliance
    gestor_uuid = Column(GUID(), ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    codigo_proyecto_fse = Column(String, nullable=True) # Proyecto FSE asignado para beneficiarios
    creado_por = Column(GUID(), ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class AsignacionFondosGestor(Base):
    """
    Tabla de registro y control de fondos FSE+ asignados por Up Spain a Gestores Sociales.
    """
    __tablename__ = 'asignacion_fondos_gestor'
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    gestor_id = Column(GUID(), ForeignKey('usuarios.id', ondelete='CASCADE'), nullable=False)
    codigo_proyecto_fse = Column(String, nullable=False, index=True)
    presupuesto_total = Column(Numeric(10, 2), nullable=False)
    presupuesto_consumido = Column(Numeric(10, 2), default=0.00, nullable=False)
    tasa_cofinanciacion = Column(Numeric(5, 2), nullable=False) # Ej: 0.70 para el 70%
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Restricción a nivel de BD para compliance financiero estricto
    __table_args__ = (
        CheckConstraint('presupuesto_consumido <= presupuesto_total', name='check_presupuesto_consumido_limit'),
    )

class ProductoSupermercado(Base):
    """
    Modelo de Producto asignado a un supermercado específico.
    """
    __tablename__ = 'supermarket_products_sqlalchemy'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    supermercado_id = Column(String, nullable=False, index=True)
    codigo_barras = Column(String, nullable=False, index=True) # Código EAN de 13 dígitos
    nombre = Column(String, nullable=False)
    precio = Column(Numeric(10, 2), nullable=False)
    categoria_fse = Column(String, nullable=True) # Clasificación FSE+ (alimentación básica permitida)
    palabras_clave_ocr = Column(JSON, nullable=True) # Array JSON de palabras clave para ayudar al matcher (ej. ['LCH', 'LECHE'])
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class AuditoriaTransaccion(Base):
    """
    Pista de Auditoría Append-Only para el seguimiento de fondos FSE+ (Unión Europea).
    
    🔒 CUMPLIMIENTO CON RGPD Y REQUISITOS DE AUDITORÍA DE LA UE:
    1. Pista inmutable y Append-Only: 
       Los registros de auditoría de fondos públicos no deben alterarse.
    2. Derecho al olvido (Art. 17 RGPD) vs Retención de Auditoría:
       La relación con el usuario está configurada con 'ondelete=SET NULL'.
       Si un usuario ejerce su derecho al olvido y es eliminado de la tabla 'usuarios':
       - El registro de auditoría en esta tabla permanece intacto para los inspectores europeos.
       - La columna 'usuario_uuid' pasa a ser NULL de forma irreversible.
       - El registro queda 100% anonimizado de forma definitiva, cumpliendo con la Ley de Subvenciones
         sin mantener ningún dato personal directo o indirecto del beneficiario.
    """
    __tablename__ = 'auditoria_transacciones_sqlalchemy'
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    usuario_uuid = Column(GUID(), ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    supermercado_id = Column(String, nullable=False, index=True)
    total = Column(Numeric(10, 2), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    estado = Column(String, nullable=False) # 'APPROVED', 'DISCREPANCY', 'REJECTED'
