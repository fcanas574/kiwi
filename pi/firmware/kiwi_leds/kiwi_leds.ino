// Kiwi · LEDs por USB serial (driver `leds.driver: serial` de la Raspberry Pi).
// Opcional: usar solo si los LEDs se conectan a un ESP32/Arduino en lugar de los GPIO de la Pi.
//
// Protocolo (115200 baudios), una línea por cambio:
//   <posición 1..9> <RRGGBB>\n     p. ej. "3 006DFF" enciende la posición 3; "3 000000" la apaga.
// La Pi ya calcula el parpadeo; la placa solo enciende o apaga.
// Con LEDs simples cualquier color distinto de 000000 enciende el pin.

const uint8_t PINS[] = {2, 3, 4, 5, 6, 7, 8, 9, 10};   // Arduino Uno/Nano. En ESP32 usar p. ej. 13,12,14,27,26,25,33,32,15
const uint8_t COUNT = sizeof(PINS) / sizeof(PINS[0]);
String line;

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < COUNT; i++) {
    pinMode(PINS[i], OUTPUT);
    digitalWrite(PINS[i], LOW);
  }
  Serial.println("kiwi-leds listo");
}

void apply(const String &command) {
  int space = command.indexOf(' ');
  if (space < 1) return;
  int slot = command.substring(0, space).toInt();
  long color = strtol(command.substring(space + 1).c_str(), nullptr, 16);
  if (slot < 1 || slot > COUNT) return;
  digitalWrite(PINS[slot - 1], color != 0 ? HIGH : LOW);
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      line.trim();
      apply(line);
      line = "";
    } else if (line.length() < 32) {
      line += c;
    }
  }
}
