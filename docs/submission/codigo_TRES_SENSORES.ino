#include <Wire.h> // Librería I2C para el MPU6050

// ================================================================
// 1. VARIABLES Y PINES: SENSOR RESISTIVO (Divisor de tensión)
// ================================================================
const int sensorResPin = A0;     // Pin analógico A0[cite: 1]
const float Rfija = 10000.0;     // Resistencia fija de 10k ohms[cite: 1]

// ================================================================
// 2. VARIABLES Y PINES: SENSOR EMG (Actividad muscular)
// ================================================================
const int emgPin = A1;           // Cambiado a A1 para no chocar con el sensor en A0
int emgValue = 0;                // Variable para almacenar la lectura EMG[cite: 3]

// ================================================================
// 3. VARIABLES Y CONFIGURACIÓN: GIROSCOPIO/ACELERÓMETRO (MPU6050)
// ================================================================
const int MPU = 0x68;            // Dirección I2C del MPU6050[cite: 2]
float accX, accY, accZ;          // Datos del acelerómetro[cite: 2]
float gyroX, gyroY, gyroZ;       // Datos del giroscopio[cite: 2]
float angleX = 0, angleY = 0;    // Ángulos calculados[cite: 2]
float offsetX = 0, offsetY = 0, offsetZ = 0; // Calibración inicial[cite: 2]
unsigned long previousTime;      // Control de tiempo para el filtro complementario[cite: 2]


void setup() {
  // --- Configuración General ---
  Serial.begin(9600);            // Iniciamos monitor serie una sola vez[cite: 1, 2, 3]

  // --- Configuración MPU6050 ---
  Wire.begin();                  // Iniciamos comunicación I2C[cite: 2]

  // Despertar el MPU6050
  Wire.beginTransmission(MPU);
  Wire.write(0x6B);
  Wire.write(0);
  Wire.endTransmission();        //[cite: 2]

  // Configurar rango del giroscopio (+/- 250 deg/s)
  Wire.beginTransmission(MPU);
  Wire.write(0x1B);
  Wire.write(0);
  Wire.endTransmission();        //[cite: 2]

  // Configurar rango del acelerómetro (+/- 2g)
  Wire.beginTransmission(MPU);
  Wire.write(0x1C);
  Wire.write(0);
  Wire.endTransmission();        //[cite: 2]

  delay(1000);                   // Esperar estabilización[cite: 2]

  // Calibración del giroscopio (500 muestras - mantener sensor quieto)
  Serial.println("Calibrando MPU6050, no mover...");
  for (int i = 0; i < 500; i++) {
    Wire.beginTransmission(MPU);
    Wire.write(0x43);
    Wire.endTransmission(false);
    Wire.requestFrom(MPU, 6, true);

    int16_t gx = Wire.read() << 8 | Wire.read();
    int16_t gy = Wire.read() << 8 | Wire.read();
    int16_t gz = Wire.read() << 8 | Wire.read();

    offsetX += gx / 131.0;
    offsetY += gy / 131.0;
    offsetZ += gz / 131.0;

    delay(5);
  }
  offsetX /= 500;
  offsetY /= 500;
  offsetZ /= 500;                //[cite: 2]

  previousTime = micros();       // Iniciamos el reloj para el cálculo de ángulos[cite: 2]
  Serial.println("Calibracion lista.");
}


void loop() {
  // ================================================================
  // LECTURA Y CÁLCULO 1: SENSOR RESISTIVO
  // ================================================================
  int lecturaRes = analogRead(sensorResPin); //[cite: 1]
  float voltaje = lecturaRes * (5.0 / 1023.0); //[cite: 1]
  // Evitamos división por cero si el voltaje llega a 5V exactos
  float resistencia = (voltaje < 5.0) ? (Rfija * voltaje / (5.0 - voltaje)) : 0; //[cite: 1]


  // ================================================================
  // LECTURA Y CÁLCULO 2: SENSOR EMG
  // ================================================================
  emgValue = analogRead(emgPin); //[cite: 3]


  // ================================================================
  // LECTURA Y CÁLCULO 3: MPU6050 (Acelerómetro y Giroscopio)
  // ================================================================
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU, 14, true); //[cite: 2]

  int16_t ax = Wire.read() << 8 | Wire.read();
  int16_t ay = Wire.read() << 8 | Wire.read();
  int16_t az = Wire.read() << 8 | Wire.read();
  Wire.read(); Wire.read(); // Descartamos los 2 bytes de temperatura[cite: 2]
  int16_t gx = Wire.read() << 8 | Wire.read();
  int16_t gy = Wire.read() << 8 | Wire.read();
  int16_t gz = Wire.read() << 8 | Wire.read(); //[cite: 2]

  // Conversión a unidades físicas
  accX = ax / 16384.0;
  accY = ay / 16384.0;
  accZ = az / 16384.0; //[cite: 2]

  gyroX = gx / 131.0 - offsetX;
  gyroY = gy / 131.0 - offsetY;
  gyroZ = gz / 131.0 - offsetZ; //[cite: 2]

  // Cálculo de ángulos con acelerómetro
  float accAngleX = atan2(accY, accZ) * 180.0 / PI;
  float accAngleY = atan2(-accX, sqrt(accY * accY + accZ * accZ)) * 180.0 / PI; //[cite: 2]

  // Cálculo de tiempo transcurrido (dt)
  unsigned long currentTime = micros();
  float dt = (currentTime - previousTime) / 1000000.0;
  previousTime = currentTime; //[cite: 2]

  // Filtro complementario para obtener ángulos estables
  angleX = 0.98 * (angleX + gyroX * dt) + 0.02 * accAngleX;
  angleY = 0.98 * (angleY + gyroY * dt) + 0.02 * accAngleY; //[cite: 2]


  // ================================================================
  // IMPRESIÓN DE TODOS LOS DATOS EN EL MONITOR SERIE
  // ================================================================
  // 1. Datos Sensor Resistivo
  Serial.print("[RESISTIVO] Voltaje: "); Serial.print(voltaje);
  Serial.print(" V | Resistencia: "); Serial.print(resistencia); Serial.println(" ohms"); //[cite: 1]

  // 2. Datos Sensor EMG
  Serial.print("[EMG] Valor crudo: "); Serial.println(emgValue); //[cite: 3]

  // 3. Datos MPU6050
  Serial.print("[MPU6050] Acel X: "); Serial.print(accX);
  Serial.print(" Y: "); Serial.print(accY);
  Serial.print(" Z: "); Serial.println(accZ); //[cite: 2]

  Serial.print("[MPU6050] Giro X: "); Serial.print(gyroX);
  Serial.print(" Y: "); Serial.print(gyroY);
  Serial.print(" Z: "); Serial.println(gyroZ); //[cite: 2]

  Serial.print("[MPU6050] Angulo X: "); Serial.print(angleX);
  Serial.print(" deg | Angulo Y: "); Serial.print(angleY); Serial.println(" deg"); //[cite: 2]

  Serial.println("================================================");

  // Un solo delay al final para controlar la velocidad de todo el ciclo
  delay(200); //[cite: 2]
}
