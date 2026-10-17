def convert(value, source, target):
    factors = {"kg": 1000, "g": 1, "mg": 0.001}
    return str(float(value) * factors[source] / factors[target])
