# Pegar y ejecutar después de cargar el modelo en Colab.
# Luego ejecutar sección 4 y cargar references.zip en sección 5.
CATEGORIES = {
    'soldering_station': 'An electronics soldering and hot air rework station with a control unit and iron holder',
    'digital_multimeter': 'A handheld digital multimeter with a screen and rotary selector',
    'bench_power_supply': 'A laboratory bench DC power supply with output terminals and controls',
    'servo_motor': 'A small blue micro servo motor with a three-wire cable',
    'oscilloscope': 'A digital oscilloscope with a waveform screen and input connectors',
    'function_generator': 'A laboratory function generator with frequency controls and signal outputs',
    'variable_capacitor': 'A laboratory variable parallel plate capacitor with circular metal plates on a base',
    'capacitors': 'A small cylindrical electrolytic capacitor with two wire leads',
    'power_adapter': 'A rectangular AC DC power adapter with an integrated output cable',
    'multimeter_box': 'A yellow orange cardboard retail box for an AstroAI digital multimeter',
    'solder': 'An electric soldering iron with a handle and pointed metal tip',
    'otros': 'An everyday object that is not one of the laboratory tools, such as household items, hand tools or bare circuit boards',
}
MIN_SIMILARITY = None
MIN_MARGIN = None
print('Doce categorías configuradas (otros = referencias negativas). Ejecutar sección 4 y luego cargar references.zip en sección 5.')
