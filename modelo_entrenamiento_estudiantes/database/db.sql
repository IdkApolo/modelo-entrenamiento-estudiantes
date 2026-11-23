CREATE DATABASE IF NOT EXISTS reconocimiento_facial;
USE reconocimiento_facial;

CREATE TABLE IF NOT EXISTS img_dataset (
    img_id INT(11) NOT NULL,
    img_person VARCHAR(3) NOT NULL,
    PRIMARY KEY (img_id)
);

CREATE TABLE IF NOT EXISTS prs_mstr (
    prs_nbr VARCHAR(3) NOT NULL,
    prs_name VARCHAR(50) NOT NULL,
    prs_skill VARCHAR(20) NOT NULL,
    seccion VARCHAR(32) NULL,
    prs_active VARCHAR(1) NOT NULL DEFAULT 'Y',
    prs_added DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (prs_nbr)
);

-- Agrega la columna en instalaciones existentes sin fallar si ya fue aplicada.
SET @vidvig_add_seccion = (
    SELECT IF(
        COUNT(*) = 0,
        'ALTER TABLE prs_mstr ADD COLUMN seccion VARCHAR(32) NULL AFTER prs_skill',
        'SELECT 1'
    )
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'prs_mstr'
      AND COLUMN_NAME = 'seccion'
);
PREPARE vidvig_stmt FROM @vidvig_add_seccion;
EXECUTE vidvig_stmt;
DEALLOCATE PREPARE vidvig_stmt;

CREATE TABLE IF NOT EXISTS asistencia (
    id INT(11) NOT NULL AUTO_INCREMENT,
    prs_nbr VARCHAR(3) NULL,
    fecha_hora DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confianza INT(3) NOT NULL DEFAULT 0,
    estado VARCHAR(20) NOT NULL DEFAULT 'desconocido',
    PRIMARY KEY (id),
    INDEX idx_asistencia_fecha_hora (fecha_hora),
    INDEX idx_asistencia_persona (prs_nbr),
    INDEX idx_asistencia_estado (estado)
);
