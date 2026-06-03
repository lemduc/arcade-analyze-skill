graph TD
    Default["Default\n(21 entities)"]
    Algorithms["Algorithms\n(65 entities)"]
    Exporters["Exporters\n(15 entities)"]
    Parsers["Parsers\n(58 entities)"]
    Tools["Tools\n(41 entities)"]
    Algorithms --> Tools
    Default --> Algorithms
    Default --> Parsers
    Exporters --> Tools
    Tools --> Default
    Tools --> Parsers