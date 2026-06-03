graph TD
    Default["Default\n(71 entities)"]
    Antipattern["Antipattern\n(42 entities)"]
    Clustering["Clustering\n(343 entities)"]
    Facts["Facts\n(145 entities)"]
    Functiongraph["Functiongraph\n(27 entities)"]
    Jira["Jira\n(4 entities)"]
    Metrics["Metrics\n(110 entities)"]
    Topics["Topics\n(94 entities)"]
    Util["Util\n(52 entities)"]
    Visualization["Visualization\n(41 entities)"]
    Util2["Util2\n(38 entities)"]
    Json["Json\n(91 entities)"]
    Matrix["Matrix\n(20 entities)"]
    Antipattern --> Clustering
    Antipattern --> Json
    Antipattern --> Topics
    Antipattern --> Util
    Clustering --> Facts
    Clustering --> Json
    Clustering --> Metrics
    Clustering --> Topics
    Clustering --> Util
    Clustering --> Util2
    Clustering --> Visualization
    Default --> Clustering
    Facts --> Antipattern
    Facts --> Clustering
    Facts --> Functiongraph
    Facts --> Json
    Facts --> Util
    Facts --> Util2
    Json --> Facts
    Metrics --> Clustering
    Metrics --> Default
    Metrics --> Json
    Metrics --> Matrix
    Metrics --> Util
    Metrics --> Util2
    Topics --> Clustering
    Topics --> Json
    Topics --> Util
    Topics --> Util2
    Topics --> Visualization
    Util --> Clustering
    Util --> Facts
    Util --> Util2
    Visualization --> Clustering
    Visualization --> Topics
    Visualization --> Util