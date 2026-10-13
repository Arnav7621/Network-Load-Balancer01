/* =========================================================
   VANTORA LOAD BALANCER DASHBOARD
   ========================================================= */


/* =========================================================
   CONFIGURATION
   ========================================================= */

/*
   The dashboard talks to its own server, which forwards these requests to
   the load balancer (see dashboard/app.py and its LB_URL setting).
   Relative URLs work wherever the dashboard is served from, including
   inside Docker.
*/

const STATS_URL =
    "/api/stats";

const ALGORITHM_URL =
    "/api/algorithm";


/* =========================================================
   EVENT HISTORY
   ========================================================= */

const eventHistory = [];

const MAX_EVENTS = 20;

let previousBackendStatus = {};

let previousCircuitStatus = {};

let previousAlgorithm = null;


/* =========================================================
   LIVE CHART DATA
   ========================================================= */

let trafficChart = null;

let latencyChart = null;


const chartHistory = {

    labels: [],

    traffic: {
        9001: [],
        9002: [],
        9003: []
    },

    latency: {
        9001: [],
        9002: [],
        9003: []
    }
};


let previousChartRequests = {

    9001: 0,
    9002: 0,
    9003: 0

};


let previousChartTime = null;

const MAX_CHART_POINTS = 20;


let lastStats = null;

let previousRequestCounts = {};


/* =========================================================
   HELPER FUNCTIONS
   ========================================================= */

function formatNumber(value) {

    return Number(
        value || 0
    ).toLocaleString();
}


function formatLatency(value) {

    const latency =
        Number(value || 0);

    return `${latency.toFixed(3)}s`;
}


function getBackend(stats, port) {

    if (
        !stats ||
        !stats.backends
    ) {
        return null;
    }

    return stats.backends.find(
        backend =>
            Number(backend.port) ===
            Number(port)
    );
}


/* =========================================================
   EVENT HISTORY
   ========================================================= */

function addEvent(
    type,
    message
) {

    const now =
        new Date();


    eventHistory.unshift({

        type: type,

        message: message,

        time:
            now.toLocaleTimeString(
                [],
                {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit"
                }
            )
    });


    if (
        eventHistory.length >
        MAX_EVENTS
    ) {

        eventHistory.pop();
    }


    renderEvents();
}


/* =========================================================
   EVENT DETECTION
   ========================================================= */

function detectEvents(stats) {

    if (
        !stats ||
        !stats.backends
    ) {
        return;
    }


    /*
       First dashboard update.

       Store the current state without
       generating false events.
    */

    if (
        Object.keys(
            previousBackendStatus
        ).length === 0
    ) {

        stats.backends.forEach(
            backend => {

                previousBackendStatus[
                    backend.port
                ] =
                    backend.status;


                previousCircuitStatus[
                    backend.port
                ] =
                    backend.circuit
                        ? backend.circuit.state
                        : "CLOSED";

            }
        );


        previousAlgorithm =
            stats.algorithm;


        return;
    }


    /*
       ---------------------------------------------
       Backend status changes
       ---------------------------------------------
    */

    stats.backends.forEach(
        backend => {

            const port =
                backend.port;


            const currentStatus =
                backend.status;


            const oldStatus =
                previousBackendStatus[
                    port
                ];


            /*
               Backend went DOWN
            */

            if (
                oldStatus === "UP" &&
                currentStatus === "DOWN"
            ) {

                addEvent(
                    "critical",
                    `Backend-${port} went DOWN`
                );
            }


            /*
               Backend recovered
            */

            if (
                oldStatus === "DOWN" &&
                currentStatus === "UP"
            ) {

                addEvent(
                    "success",
                    `Backend-${port} recovered`
                );
            }


            previousBackendStatus[
                port
            ] =
                currentStatus;


            /*
               ---------------------------------------------
               Circuit breaker changes
               ---------------------------------------------
            */

            const currentCircuit =
                backend.circuit
                    ? backend.circuit.state
                    : "CLOSED";


            const oldCircuit =
                previousCircuitStatus[
                    port
                ];


            /*
               Circuit opened
            */

            if (
                oldCircuit === "CLOSED" &&
                currentCircuit === "OPEN"
            ) {

                addEvent(
                    "critical",
                    `Backend-${port} circuit breaker OPEN`
                );
            }


            /*
               Circuit entered HALF_OPEN
            */

            if (
                oldCircuit === "OPEN" &&
                currentCircuit === "HALF_OPEN"
            ) {

                addEvent(
                    "info",
                    `Backend-${port} circuit testing recovery`
                );
            }


            /*
               Circuit recovered
            */

            if (
                (
                    oldCircuit === "OPEN" ||
                    oldCircuit === "HALF_OPEN"
                ) &&
                currentCircuit === "CLOSED"
            ) {

                addEvent(
                    "success",
                    `Backend-${port} circuit breaker CLOSED`
                );
            }


            previousCircuitStatus[
                port
            ] =
                currentCircuit;

        }
    );


    /*
       ---------------------------------------------
       Routing algorithm change
       ---------------------------------------------
    */

    if (
        previousAlgorithm !== null &&
        stats.algorithm !==
            previousAlgorithm
    ) {

        addEvent(
            "info",
            `Routing algorithm changed to ${stats.algorithm}`
        );
    }


    previousAlgorithm =
        stats.algorithm;
}


/* =========================================================
   RENDER EVENTS
   ========================================================= */

function renderEvents() {

    const eventsList =
        document.getElementById(
            "events-list"
        );


    const eventsCount =
        document.getElementById(
            "events-count"
        );


    if (!eventsList) {
        return;
    }


    /*
       No events
    */

    if (
        eventHistory.length === 0
    ) {

        eventsList.innerHTML = `

            <div class="event-empty">

                <div class="event-empty-icon">
                    ✓
                </div>

                <div>
                    No events recorded
                </div>

            </div>

        `;


        if (eventsCount) {

            eventsCount.textContent =
                "0 events";
        }


        return;
    }


    /*
       Render event history
    */

    eventsList.innerHTML =
        eventHistory.map(
            event => {

                return `

                    <div class="event-item">

                        <div
                            class="event-icon ${event.type}"
                        >
                            <span></span>
                        </div>


                        <div class="event-content">

                            <div class="event-message">
                                ${event.message}
                            </div>


                            <div class="event-time">
                                ${event.time}
                            </div>

                        </div>

                    </div>

                `;

            }
        ).join("");


    /*
       Event counter
    */

    if (eventsCount) {

        eventsCount.textContent =
            `${eventHistory.length} ${
                eventHistory.length === 1
                    ? "event"
                    : "events"
            }`;
    }
}


/* =========================================================
   KPI SECTION
   ========================================================= */

function updateKPIs(stats) {

    const backends =
        stats.backends || [];


    let totalRequests = 0;

    let totalActive = 0;

    let totalErrors = 0;

    let healthyBackends = 0;

    let totalResponseTime = 0;


    backends.forEach(
        backend => {

            const requests =
                Number(
                    backend.total_requests || 0
                );


            const active =
                Number(
                    backend.active_connections || 0
                );


            const errors =
                Number(
                    backend.errors || 0
                );


            const responseTime =
                Number(
                    backend.avg_response_time || 0
                );


            totalRequests +=
                requests;


            totalActive +=
                active;


            totalErrors +=
                errors;


            if (
                backend.status === "UP"
            ) {

                healthyBackends++;
            }


            totalResponseTime +=
                responseTime * requests;

        }
    );


    let averageLatency = 0;


    if (
        totalRequests > 0
    ) {

        averageLatency =
            totalResponseTime /
            totalRequests;
    }


    const totalBackends =
        backends.length;


    const requestsElement =
        document.getElementById(
            "total-requests"
        );


    if (requestsElement) {

        requestsElement.textContent =
            formatNumber(
                totalRequests
            );
    }


    const activeElement =
        document.getElementById(
            "active-connections"
        );


    if (activeElement) {

        activeElement.textContent =
            formatNumber(
                totalActive
            );
    }


    const errorsElement =
        document.getElementById(
            "total-errors"
        );


    if (errorsElement) {

        errorsElement.textContent =
            formatNumber(
                totalErrors
            );
    }


    const latencyElement =
        document.getElementById(
            "avg-latency"
        );


    if (latencyElement) {

        latencyElement.textContent =
            formatLatency(
                averageLatency
            );
    }


    const healthyElement =
        document.getElementById(
            "healthy-count"
        );


    if (healthyElement) {

        healthyElement.textContent =
            `${healthyBackends}/${totalBackends}`;
    }


    /* Colour the KPI cards when the data shows a problem. */

    const errorsCard =
        errorsElement
            ? errorsElement.closest(".kpi")
            : null;

    if (errorsCard) {

        errorsCard.classList.toggle(
            "is-alert",
            totalErrors > 0
        );
    }


    const healthyCard =
        healthyElement
            ? healthyElement.closest(".kpi")
            : null;

    if (healthyCard) {

        healthyCard.classList.toggle(
            "is-alert",
            totalBackends > 0 &&
            healthyBackends === 0
        );

        healthyCard.classList.toggle(
            "is-warning",
            healthyBackends > 0 &&
            healthyBackends < totalBackends
        );
    }
}


/* =========================================================
   SYSTEM VERDICT
   ========================================================= */

function updateVerdict(stats) {

    const verdict =
        document.getElementById(
            "verdict"
        );


    const title =
        document.getElementById(
            "verdict-title"
        );


    const subtitle =
        document.getElementById(
            "verdict-subtitle"
        );


    if (
        !verdict ||
        !title ||
        !subtitle
    ) {

        return;
    }


    const backends =
        stats.backends || [];


    const healthy =
        backends.filter(
            backend =>
                backend.status === "UP"
        ).length;


    const total =
        backends.length;


    if (
        total === 0
    ) {

        title.textContent =
            "No backend data";


        subtitle.textContent =
            "Waiting for load balancer statistics";


        verdict.classList.remove(
            "healthy",
            "warning",
            "critical"
        );


        verdict.classList.add(
            "critical"
        );


        return;
    }


    if (
        healthy === total
    ) {

        title.textContent =
            "All systems operational";


        subtitle.textContent =
            `${healthy} of ${total} backends healthy`;


        verdict.classList.remove(
            "warning",
            "critical"
        );


        verdict.classList.add(
            "healthy"
        );


        return;
    }


    if (
        healthy > 0
    ) {

        title.textContent =
            "Degraded service";


        subtitle.textContent =
            `${healthy} of ${total} backends healthy`;


        verdict.classList.remove(
            "healthy",
            "critical"
        );


        verdict.classList.add(
            "warning"
        );


        return;
    }


    title.textContent =
        "No healthy backends";


    subtitle.textContent =
        "All backend servers are unavailable";


    verdict.classList.remove(
        "healthy",
        "warning"
    );


    verdict.classList.add(
        "critical"
    );
}


/* =========================================================
   GLOBAL LIVE STATUS
   ========================================================= */

function updateGlobalStatus(stats) {

    const statusContainer =
        document.querySelector(
            ".global-status"
        ) ||
        document.querySelector(
            ".live-status"
        );


    if (!statusContainer) {

        return;
    }


    const statusText =
        statusContainer.querySelector(
            "span:last-child"
        );


    const statusDot =
        statusContainer.querySelector(
            ".status-dot"
        ) ||
        statusContainer.querySelector(
            ".live-dot"
        );


    const backends =
        stats.backends || [];


    const healthy =
        backends.filter(
            backend =>
                backend.status === "UP"
        ).length;


    const total =
        backends.length;


    if (
        total === 0
    ) {

        if (statusText) {

            statusText.textContent =
                "OFFLINE";
        }


        return;
    }


    if (
        healthy === total
    ) {

        if (statusText) {

            statusText.textContent =
                "LIVE";
        }


        if (statusDot) {

            statusDot.classList.remove(
                "warning",
                "critical"
            );


            statusDot.classList.add(
                "healthy"
            );
        }


        return;
    }


    if (
        healthy > 0
    ) {

        if (statusText) {

            statusText.textContent =
                "DEGRADED";
        }


        if (statusDot) {

            statusDot.classList.remove(
                "healthy",
                "critical"
            );


            statusDot.classList.add(
                "warning"
            );
        }


        return;
    }


    if (statusText) {

        statusText.textContent =
            "OFFLINE";
    }


    if (statusDot) {

        statusDot.classList.remove(
            "healthy",
            "warning"
        );


        statusDot.classList.add(
            "critical"
        );
    }
}


/* =========================================================
   ROUTING ALGORITHM
   ========================================================= */

function updateRoutingAlgorithm(stats) {

    const algorithm =
        stats.algorithm ||
        "Unknown";


    /*
       Routing section
    */

    const routingAlgorithm =
        document.getElementById(
            "routing-algorithm"
        );


    if (routingAlgorithm) {

        routingAlgorithm.textContent =
            algorithm;
    }


    /*
       Topology section
    */

    const topologyAlgorithm =
        document.getElementById(
            "topology-algorithm"
        );


    if (topologyAlgorithm) {

        topologyAlgorithm.textContent =
            algorithm;
    }


    /*
       IMPORTANT:
       Do NOT update the entire #algorithm element.

       The #algorithm element contains:
       - #algorithm-label
       - dropdown arrow
       - #algorithm-menu

       Updating its textContent would delete
       the entire dropdown menu.
    */

    const topAlgorithmLabel =
        document.getElementById(
            "algorithm-label"
        );


    if (topAlgorithmLabel) {

        topAlgorithmLabel.textContent =
            algorithm;
    }


    /*
       Update active dropdown option
    */

    const options =
        document.querySelectorAll(
            ".algorithm-option"
        );


    options.forEach(
        option => {

            option.setAttribute(
                "aria-selected",
                String(
                    option.dataset.algorithm === algorithm
                )
            );


            if (
                option.dataset.algorithm ===
                algorithm
            ) {

                option.classList.add(
                    "active"
                );

            } else {

                option.classList.remove(
                    "active"
                );

            }

        }
    );
}


/* =========================================================
   TIMESTAMP
   ========================================================= */

function updateTimestamp() {

    const timestamp =
        document.getElementById(
            "last-updated"
        );


    if (!timestamp) {

        return;
    }


    const now =
        new Date();


    lastUpdateTime =
        now.getTime();


    timestamp.textContent =
        now.toLocaleTimeString(
            [],
            {
                hour: "numeric",
                minute: "2-digit",
                second: "2-digit"
            }
        );
}


/* =========================================================
   TOPOLOGY NODE
   ========================================================= */

function updateTopologyNode(
    stats,
    port
) {

    const backend =
        getBackend(
            stats,
            port
        );


    if (!backend) {

        return;
    }


    const node =
        document.getElementById(
            `node-${port}`
        );


    if (!node) {

        return;
    }


    const statusText =
        node.querySelector(
            ".status-text"
        );


    const statusDot =
        node.querySelector(
            ".status-dot"
        );


    const requests =
        Number(
            backend.total_requests || 0
        );


    const active =
        Number(
            backend.active_connections || 0
        );


    const isHealthy =
        backend.status === "UP";


    if (statusText) {

        statusText.textContent =
            isHealthy
                ? "UP"
                : "DOWN";
    }


    if (statusDot) {

        statusDot.classList.remove(
            "healthy",
            "warning",
            "critical",
            "up",
            "down"
        );


        if (isHealthy) {

            statusDot.classList.add(
                "healthy"
            );

        } else {

            statusDot.classList.add(
                "critical"
            );
        }
    }


    node.classList.remove(
        "healthy",
        "warning",
        "critical",
        "up",
        "down"
    );


    if (isHealthy) {

        node.classList.add(
            "healthy"
        );

    } else {

        node.classList.add(
            "critical"
        );
    }


    const requestElement =
        document.getElementById(
            `node-requests-${port}`
        );


    if (requestElement) {

        requestElement.textContent =
            formatNumber(
                requests
            );
    }


    const activeElement =
        document.getElementById(
            `node-active-${port}`
        );


    if (activeElement) {

        activeElement.textContent =
            formatNumber(
                active
            );
    }
}


/* =========================================================
   TOPOLOGY
   ========================================================= */

function updateTopology(stats) {

    const backends =
        stats.backends || [];


    updateTopologyNode(
        stats,
        9001
    );


    updateTopologyNode(
        stats,
        9002
    );


    updateTopologyNode(
        stats,
        9003
    );


    const totalRequests =
        backends.reduce(
            (
                sum,
                backend
            ) =>
                sum +
                Number(
                    backend.total_requests || 0
                ),
            0
        );


    const ingress =
        document.getElementById(
            "ingress-value"
        );


    if (ingress) {

        ingress.textContent =
            formatNumber(
                totalRequests
            );
    }


    backends.forEach(
        backend => {

            const port =
                Number(
                    backend.port
                );


            const routingElement =
                document.getElementById(
                    `routing-${port}`
                );


            if (routingElement) {

                routingElement.textContent =
                    formatNumber(
                        Number(
                            backend.total_requests || 0
                        )
                    );

                const routingRow =
                    routingElement.closest(".routing-backend");

                if (routingRow) {

                    const share =
                        totalRequests > 0
                            ? (
                                Number(
                                    backend.total_requests || 0
                                ) / totalRequests
                            ) * 100
                            : 0;

                    routingRow.style.setProperty(
                        "--share",
                        `${share.toFixed(1)}%`
                    );

                    routingRow.title =
                        `${share.toFixed(1)}% of requests`;
                }
            }

        }
    );
}


/* =========================================================
   LIVE TRAFFIC ANIMATION
   ========================================================= */

function updateTrafficAnimation(stats) {

    const backends =
        stats.backends || [];


    const flowLines =
        document.querySelectorAll(
            ".backend-flow .flow-line"
        );


    if (
        flowLines.length === 0
    ) {

        return;
    }


    backends.forEach(
        (
            backend,
            index
        ) => {

            const port =
                Number(
                    backend.port
                );


            const currentRequests =
                Number(
                    backend.total_requests || 0
                );


            const previousRequests =
                Number(
                    previousRequestCounts[
                        port
                    ] || 0
                );


            if (
                currentRequests >
                    previousRequests &&
                flowLines[index]
            ) {

                const line =
                    flowLines[index];


                line.classList.remove(
                    "traffic-active"
                );


                void line.offsetWidth;


                line.classList.add(
                    "traffic-active"
                );


                setTimeout(
                    () => {

                        line.classList.remove(
                            "traffic-active"
                        );

                    },
                    1000
                );
            }


            previousRequestCounts[
                port
            ] =
                currentRequests;

        }
    );
}


/* =========================================================
   PULSE BARS
   ========================================================= */

function createPulseBars(
    backend,
    isUp
) {

    const heights = [

        7,
        11,
        15,
        9,
        13,
        8,
        16,
        10,
        14,
        7,
        12,
        15

    ];


    let html = "";


    for (
        let i = 0;
        i < heights.length;
        i++
    ) {

        let barColor =
            "#6C7CFF";


        /*
           Backend DOWN
        */

        if (!isUp) {

            barColor =
                "#F2555A";
        }


        /*
           Backend has errors
        */

        else if (
            Number(
                backend.errors || 0
            ) > 0 &&
            i >=
                heights.length - 2
        ) {

            barColor =
                "#F5B544";
        }


        html += `

            <span
                class="pulse-bar"
                style="
                    display: block;
                    width: 5px;
                    min-width: 5px;
                    height: ${heights[i]}px;
                    min-height: ${heights[i]}px;
                    flex: 0 0 5px;
                    background-color: ${barColor};
                    border-radius: 2px;
                    opacity: 1;
                "
            ></span>

        `;
    }


    return html;
}


/* =========================================================
   CIRCUIT BREAKER DISPLAY
   ========================================================= */

function getCircuitState(backend) {

    if (
        backend &&
        backend.circuit &&
        backend.circuit.state
    ) {

        return backend.circuit.state;
    }


    return "CLOSED";
}


function getCircuitClass(state) {

    if (
        state === "OPEN"
    ) {

        return "open";
    }


    if (
        state === "HALF_OPEN"
    ) {

        return "half-open";
    }


    return "closed";
}


/* =========================================================
   BACKEND TABLE ROW
   ========================================================= */

function createBackendRow(
    backend,
    totalRequests
) {

    const row =
        document.createElement(
            "tr"
        );


    const backendPort =
        Number(
            backend.port
        );


    const backendRequests =
        Number(
            backend.total_requests || 0
        );


    const activeConnections =
        Number(
            backend.active_connections || 0
        );


    const errors =
        Number(
            backend.errors || 0
        );


    const avgResponseTime =
        Number(
            backend.avg_response_time || 0
        );


    const isUp =
        backend.status === "UP";


    const circuitState =
        getCircuitState(
            backend
        );


    const circuitClass =
        getCircuitClass(
            circuitState
        );


    let trafficShare = 0;


    if (
        totalRequests > 0
    ) {

        trafficShare =
            (
                backendRequests /
                totalRequests
            ) * 100;
    }


    /*
       BACKEND
    */

    const backendCell =
        document.createElement(
            "td"
        );


    backendCell.innerHTML = `

        <div class="backend-info">

            <strong>
                Backend-${backendPort}
            </strong>

            <span>
                ${backend.host || "127.0.0.1"}:${backendPort}
            </span>

        </div>

    `;


    /*
       STATUS
    */

    const statusCell =
        document.createElement(
            "td"
        );


    statusCell.innerHTML = `

        <div class="table-status ${
            isUp
                ? "up"
                : "down"
        }">

            <span class="status-dot"></span>

            <span>
                ${
                    isUp
                        ? "UP"
                        : "DOWN"
                }
            </span>

        </div>

    `;


    /*
       CIRCUIT
    */

    const circuitCell =
        document.createElement(
            "td"
        );


    circuitCell.innerHTML = `

        <div class="circuit-status ${circuitClass}">

            <span class="circuit-dot"></span>

            <span>
                ${circuitState}
            </span>

        </div>

    `;


    /*
       PORT
    */

    const portCell =
        document.createElement(
            "td"
        );


    portCell.innerHTML = `

        <span class="mono">
            :${backendPort}
        </span>

    `;


    /*
       REQUESTS
    */

    const requestsCell =
        document.createElement(
            "td"
        );


    requestsCell.textContent =
        formatNumber(
            backendRequests
        );


    /*
       ACTIVE
    */

    const activeCell =
        document.createElement(
            "td"
        );


    activeCell.textContent =
        formatNumber(
            activeConnections
        );


    /*
       ERRORS
    */

    const errorsCell =
        document.createElement(
            "td"
        );


    errorsCell.textContent =
        formatNumber(
            errors
        );


    /*
       AVERAGE RESPONSE
    */

    const responseCell =
        document.createElement(
            "td"
        );


    responseCell.textContent =
        formatLatency(
            avgResponseTime
        );


    /*
       TRAFFIC
    */

    const trafficCell =
        document.createElement(
            "td"
        );


    trafficCell.innerHTML = `

        <div class="traffic-cell">

            <div class="traffic-value">
                ${trafficShare.toFixed(1)}%
            </div>


            <div class="pulse-strip">

                ${createPulseBars(
                    backend,
                    isUp
                )}

            </div>

        </div>

    `;


    /*
       ADD CELLS
    */

    row.appendChild(
        backendCell
    );


    row.appendChild(
        statusCell
    );


    row.appendChild(
        circuitCell
    );


    row.appendChild(
        portCell
    );


    row.appendChild(
        requestsCell
    );


    row.appendChild(
        activeCell
    );


    row.appendChild(
        errorsCell
    );


    row.appendChild(
        responseCell
    );


    row.appendChild(
        trafficCell
    );


    const columnLabels = [
        "Backend",
        "Status",
        "Circuit",
        "Port",
        "Requests",
        "Active",
        "Errors",
        "Avg response",
        "Traffic"
    ];

    Array.from(row.children).forEach(
        (cell, index) => {

            cell.setAttribute(
                "data-label",
                columnLabels[index] || ""
            );
        }
    );


    return row;
}


/* =========================================================
   BACKEND TABLE
   ========================================================= */

function updateBackendTable(stats) {

    const table =
        document.getElementById(
            "backend-table"
        );


    const backendCount =
        document.getElementById(
            "backend-count"
        );


    const backends =
        stats.backends || [];


    if (backendCount) {

        const count =
            backends.length;


        backendCount.textContent =
            `${count} ${
                count === 1
                    ? "server"
                    : "servers"
            }`;
    }


    if (!table) {

        return;
    }


    table.innerHTML = "";


    const totalRequests =
        backends.reduce(
            (
                sum,
                backend
            ) =>
                sum +
                Number(
                    backend.total_requests || 0
                ),
            0
        );


    backends.forEach(
        backend => {

            table.appendChild(
                createBackendRow(
                    backend,
                    totalRequests
                )
            );

        }
    );
}


/* =========================================================
   FOOTER
   ========================================================= */

function updateFooter(stats) {

    const footerStatus =
        document.getElementById(
            "poll-status"
        );


    if (!footerStatus) {

        return;
    }


    footerStatus.textContent =
        refreshStatusText();
}


/* =========================================================
   UPDATE COMPLETE DASHBOARD
   ========================================================= */

function updateDashboard(stats) {

    lastStats =
        stats;


    /*
       Detect events before updating
       the rest of the dashboard.
    */

    detectEvents(
        stats
    );


    updateCharts(
        stats.backends || []
    );


    updateKPIs(
        stats
    );


    updateVerdict(
        stats
    );


    updateGlobalStatus(
        stats
    );


    updateRoutingAlgorithm(
        stats
    );


    updateTopology(
        stats
    );


    updateTrafficAnimation(
        stats
    );


    updateBackendTable(
        stats
    );


    updateFooter(
        stats
    );


    updateTimestamp();


    renderRefreshState();
}


/* =========================================================
   API ERRORS
   ========================================================= */

/*
   Turn an unsuccessful response from the dashboard server into an Error
   whose message explains what went wrong, for example:
   "HTTP 502: Cannot reach the load balancer: <LB_URL>: <reason>"
*/

async function apiError(response) {

    let message =
        `HTTP ${response.status}`;

    try {

        const body =
            await response.json();

        const reason =
            [
                body.error || body.message,
                body.detail
            ]
                .filter(Boolean)
                .join(": ");

        if (reason) {

            message =
                `${message}: ${reason}`;
        }

    } catch (ignored) {

        /* The body was not JSON: keep the HTTP status only. */
    }

    return new Error(message);
}


/*
   A TypeError from fetch() means the browser could not reach the dashboard
   server itself (it stopped, or the network is down).
*/

function describeError(error) {

    if (error instanceof TypeError) {

        return "The dashboard server is not responding";
    }

    return (
        error && error.message
    ) || "Unknown error";
}


/* =========================================================
   CONNECTION ERROR
   ========================================================= */

function showConnectionError(error) {

    const verdict =
        document.getElementById(
            "verdict"
        );


    const title =
        document.getElementById(
            "verdict-title"
        );


    const subtitle =
        document.getElementById(
            "verdict-subtitle"
        );


    const dashboardDown =
        error instanceof TypeError;


    if (title) {

        title.textContent =
            dashboardDown
                ? "Dashboard server unreachable"
                : "Load balancer unavailable";
    }


    if (subtitle) {

        subtitle.textContent =
            error
                ? describeError(error)
                : "Unable to retrieve statistics";
    }


    if (verdict) {

        verdict.classList.remove(
            "healthy",
            "warning"
        );


        verdict.classList.add(
            "critical"
        );
    }



    /* Do not show a green "healthy" figure when nothing could be read. */

    const healthyValue =
        document.getElementById(
            "healthy-count"
        );

    if (healthyValue) {

        const healthyCard =
            healthyValue.closest(".kpi");

        if (healthyCard) {

            healthyCard.classList.add(
                "is-alert"
            );
        }
    }


    const footerStatus =
        document.getElementById(
            "poll-status"
        );


    if (footerStatus) {

        footerStatus.textContent =
            "Connection error";
    }


    const liveStatus =
        document.querySelector(
            ".live-status"
        ) ||
        document.querySelector(
            ".global-status"
        );


    if (liveStatus) {

        const liveText =
            liveStatus.querySelector(
                "span:last-child"
            );


        if (liveText) {

            liveText.textContent =
                "OFFLINE";
        }
    }
}


/* =========================================================
   FETCH STATISTICS
   ========================================================= */

async function fetchStats() {

    try {

        const response =
            await fetch(
                STATS_URL,
                {
                    method: "GET",
                    cache: "no-store"
                }
            );


        if (!response.ok) {

            throw await apiError(response);
        }


        const stats =
            await response.json();


        updateDashboard(
            stats
        );

    } catch (error) {

        console.error(
            "Failed to fetch load balancer stats:",
            error
        );


        showConnectionError(error);
    }
}


/* =========================================================
   MONITORING LOOP
   ========================================================= */

/* =========================================================
   REFRESH CONTROLS
   Manual refresh, pause/resume and the auto-refresh interval.
   Every refresh calls the same /api/stats request as before.
   ========================================================= */

const REFRESH_OPTIONS_MS = [1000, 2000, 5000, 10000, 30000];

const REFRESH_DEFAULT_MS = 2000;

const REFRESH_STORAGE_KEY = "vantora.refreshMs";

let refreshMs = loadRefreshInterval();

let refreshPaused = false;

let refreshTimer = null;

let fetchInFlight = false;

let lastUpdateTime = null;


function loadRefreshInterval() {

    try {

        const saved =
            Number(
                window.localStorage.getItem(
                    REFRESH_STORAGE_KEY
                )
            );

        if (
            REFRESH_OPTIONS_MS.includes(saved)
        ) {

            return saved;
        }

    } catch (ignored) {

        /* Browser storage is not available: use the default. */
    }

    return REFRESH_DEFAULT_MS;
}


function saveRefreshInterval(milliseconds) {

    try {

        window.localStorage.setItem(
            REFRESH_STORAGE_KEY,
            String(milliseconds)
        );

    } catch (ignored) {

        /* Not saved; the choice still applies to this page. */
    }
}


async function refreshNow() {

    /* Never start a second request while one is still running. */

    if (fetchInFlight) {

        return;
    }

    fetchInFlight = true;

    const button =
        document.getElementById(
            "refresh-now"
        );

    if (button) {

        button.classList.add(
            "is-busy"
        );
    }

    try {

        /* Keep the spinner visible long enough to be noticed. */

        await Promise.all([
            fetchStats(),
            new Promise(
                resolve => setTimeout(resolve, 400)
            )
        ]);

    } finally {

        fetchInFlight = false;

        if (button) {

            button.classList.remove(
                "is-busy"
            );
        }
    }
}


function scheduleRefresh() {

    if (refreshTimer !== null) {

        clearInterval(
            refreshTimer
        );

        refreshTimer = null;
    }

    if (!refreshPaused) {

        refreshTimer =
            setInterval(
                refreshNow,
                refreshMs
            );
    }
}


function setRefreshPaused(paused) {

    refreshPaused = paused;

    scheduleRefresh();

    renderRefreshState();

    if (!paused) {

        refreshNow();
    }
}


function setRefreshInterval(milliseconds) {

    if (
        !REFRESH_OPTIONS_MS.includes(milliseconds)
    ) {

        return;
    }

    refreshMs = milliseconds;

    saveRefreshInterval(milliseconds);

    scheduleRefresh();

    renderRefreshState();
}


function refreshStatusText() {

    return refreshPaused
        ? "Auto-refresh paused"
        : `Refreshing every ${refreshMs / 1000}s`;
}


function updateAge() {

    const element =
        document.getElementById(
            "last-updated-age"
        );

    if (!element) {

        return;
    }

    if (lastUpdateTime === null) {

        element.textContent = "";

        return;
    }

    const seconds =
        Math.max(
            0,
            Math.round(
                (Date.now() - lastUpdateTime) / 1000
            )
        );

    element.textContent =
        refreshPaused
            ? `paused · ${seconds}s ago`
            : `${seconds}s ago`;
}


function renderRefreshState() {

    document.documentElement.setAttribute(
        "data-refresh",
        refreshPaused ? "paused" : "live"
    );

    const toggle =
        document.getElementById(
            "refresh-toggle"
        );

    if (toggle) {

        const label =
            refreshPaused
                ? "Resume auto-refresh"
                : "Pause auto-refresh";

        toggle.setAttribute(
            "aria-pressed",
            String(refreshPaused)
        );

        toggle.setAttribute(
            "aria-label",
            label
        );

        toggle.title = label;
    }

    const select =
        document.getElementById(
            "refresh-interval"
        );

    if (
        select &&
        Number(select.value) !== refreshMs
    ) {

        select.value = String(refreshMs);
    }

    const status =
        document.getElementById(
            "live-status"
        );

    if (status) {

        status.classList.toggle(
            "paused",
            refreshPaused
        );

        const text =
            status.querySelector(
                "span:last-child"
            );

        if (text && refreshPaused) {

            text.textContent = "PAUSED";

        } else if (
            text &&
            text.textContent.trim() === "PAUSED"
        ) {

            text.textContent = "LIVE";
        }
    }

    document.querySelectorAll(
        ".live-label"
    ).forEach(
        element => {

            element.textContent =
                refreshPaused
                    ? "PAUSED"
                    : "LIVE";
        }
    );

    const footerStatus =
        document.getElementById(
            "poll-status"
        );

    if (
        footerStatus &&
        footerStatus.textContent !== "Connection error"
    ) {

        footerStatus.textContent =
            refreshStatusText();
    }

    updateAge();
}


function initializeRefreshControls() {

    const refreshButton =
        document.getElementById(
            "refresh-now"
        );

    const toggleButton =
        document.getElementById(
            "refresh-toggle"
        );

    const intervalSelect =
        document.getElementById(
            "refresh-interval"
        );

    if (refreshButton) {

        refreshButton.addEventListener(
            "click",
            refreshNow
        );
    }

    if (toggleButton) {

        toggleButton.addEventListener(
            "click",
            () => setRefreshPaused(
                !refreshPaused
            )
        );
    }

    if (intervalSelect) {

        intervalSelect.value =
            String(refreshMs);

        intervalSelect.addEventListener(
            "change",
            () => setRefreshInterval(
                Number(intervalSelect.value)
            )
        );
    }

    renderRefreshState();
}


/* =========================================================
   TOAST MESSAGES
   ========================================================= */

function showToast(message, type = "info", duration = 4500) {

    const region =
        document.getElementById(
            "toast-region"
        );

    if (!region) {

        return;
    }

    const toast =
        document.createElement("div");

    toast.className =
        `toast ${type}`;

    toast.setAttribute(
        "role",
        type === "error" ? "alert" : "status"
    );

    toast.textContent = message;

    region.appendChild(toast);

    while (region.children.length > 4) {

        region.removeChild(
            region.firstChild
        );
    }

    setTimeout(
        () => toast.remove(),
        duration
    );
}


/* =========================================================
   ALGORITHM SELECTOR: KEYBOARD AND SCREEN-READER SUPPORT
   ========================================================= */

function enhanceAlgorithmSelectorAccessibility() {

    const selector =
        document.getElementById(
            "algorithm"
        );

    const trigger =
        document.getElementById(
            "algorithm-trigger"
        );

    if (!selector || !trigger) {

        return;
    }

    new MutationObserver(
        () => trigger.setAttribute(
            "aria-expanded",
            String(
                selector.classList.contains("open")
            )
        )
    ).observe(
        selector,
        {
            attributes: true,
            attributeFilter: ["class"]
        }
    );

    selector.addEventListener(
        "keydown",
        event => {

            const isOpen =
                selector.classList.contains("open");

            if (
                event.key === "Escape" &&
                isOpen
            ) {

                event.preventDefault();

                selector.classList.remove("open");

                trigger.focus();

                return;
            }

            if (
                event.key !== "ArrowDown" &&
                event.key !== "ArrowUp"
            ) {

                return;
            }

            event.preventDefault();

            if (!isOpen) {

                trigger.click();
            }

            const options =
                Array.from(
                    selector.querySelectorAll(
                        ".algorithm-option"
                    )
                );

            const step =
                event.key === "ArrowDown" ? 1 : -1;

            const current =
                options.indexOf(
                    document.activeElement
                );

            const next =
                current === -1
                    ? (step === 1 ? 0 : options.length - 1)
                    : (current + step + options.length) %
                        options.length;

            options[next].focus();
        }
    );
}


function startMonitoring() {

    initializeRefreshControls();

    refreshNow();

    scheduleRefresh();

    setInterval(
        updateAge,
        1000
    );
}


/* =========================================================
   CHART INITIALIZATION
   ========================================================= */

function initializeCharts() {

    /*
       Chart.js is loaded from a CDN. Without internet access it is
       missing: skip the charts so the rest of the dashboard keeps working.
    */

    if (typeof Chart === "undefined") {

        console.warn(
            "Chart.js did not load (no internet access?). " +
            "The charts are disabled; all other panels still work."
        );

        return;
    }


    const trafficCanvas =
        document.getElementById(
            "trafficChart"
        );


    const latencyCanvas =
        document.getElementById(
            "latencyChart"
        );


    if (
        !trafficCanvas ||
        !latencyCanvas
    ) {

        console.warn(
            "Chart canvas elements not found."
        );


        return;
    }


    /*
       -----------------------------------------------
       Traffic Chart
       -----------------------------------------------
    */

    trafficChart =
        new Chart(
            trafficCanvas,
            {

                type: "line",


                data: {

                    labels: [],


                    datasets: [

                        {
                            label:
                                "Backend 9001",

                            data: [],

                            borderColor:
                                "#6C7CFF",

                            backgroundColor:
                                "rgba(108, 124, 255, 0.08)",

                            borderWidth: 2,

                            tension: 0.35,

                            fill: false,

                            pointRadius: 2,

                            pointHoverRadius: 4
                        },


                        {
                            label:
                                "Backend 9002",

                            data: [],

                            borderColor:
                                "#4CC9E0",

                            backgroundColor:
                                "rgba(76, 201, 224, 0.08)",

                            borderWidth: 2,

                            tension: 0.35,

                            fill: false,

                            pointRadius: 2,

                            pointHoverRadius: 4
                        },


                        {
                            label:
                                "Backend 9003",

                            data: [],

                            borderColor:
                                "#B58CFF",

                            backgroundColor:
                                "rgba(181, 140, 255, 0.08)",

                            borderWidth: 2,

                            tension: 0.35,

                            fill: false,

                            pointRadius: 2,

                            pointHoverRadius: 4
                        }

                    ]
                },


                options: {

                    responsive: true,

                    maintainAspectRatio: false,

                    animation: false,


                    interaction: {

                        intersect: false,

                        mode: "index"
                    },


                    plugins: {

                        legend: {

                            position: "bottom",


                            labels: {

                                color:
                                    "#9AA6BC",

                                usePointStyle:
                                    true,

                                pointStyle:
                                    "circle",

                                padding: 20
                            }
                        }
                    },


                    scales: {

                        x: {

                            ticks: {

                                color:
                                    "#66738C",

                                maxTicksLimit: 8
                            },


                            grid: {

                                color:
                                    "rgba(35, 44, 61, 0.6)"
                            }
                        },


                        y: {

                            beginAtZero:
                                true,


                            ticks: {

                                color:
                                    "#66738C"
                            },


                            grid: {

                                color:
                                    "rgba(35, 44, 61, 0.6)"
                            }
                        }
                    }
                }
            }
        );


    /*
       -----------------------------------------------
       Latency Chart
       -----------------------------------------------
    */

    latencyChart =
        new Chart(
            latencyCanvas,
            {

                type: "line",


                data: {

                    labels: [],


                    datasets: [

                        {
                            label:
                                "Backend 9001",

                            data: [],

                            borderColor:
                                "#6C7CFF",

                            borderWidth: 2,

                            tension: 0.35,

                            fill: false,

                            pointRadius: 2,

                            pointHoverRadius: 4
                        },


                        {
                            label:
                                "Backend 9002",

                            data: [],

                            borderColor:
                                "#4CC9E0",

                            borderWidth: 2,

                            tension: 0.35,

                            fill: false,

                            pointRadius: 2,

                            pointHoverRadius: 4
                        },


                        {
                            label:
                                "Backend 9003",

                            data: [],

                            borderColor:
                                "#B58CFF",

                            borderWidth: 2,

                            tension: 0.35,

                            fill: false,

                            pointRadius: 2,

                            pointHoverRadius: 4
                        }

                    ]
                },


                options: {

                    responsive: true,

                    maintainAspectRatio: false,

                    animation: false,


                    interaction: {

                        intersect: false,

                        mode: "index"
                    },


                    plugins: {

                        legend: {

                            position: "bottom",


                            labels: {

                                color:
                                    "#9AA6BC",

                                usePointStyle:
                                    true,

                                pointStyle:
                                    "circle",

                                padding: 20
                            }
                        }
                    },


                    scales: {

                        x: {

                            ticks: {

                                color:
                                    "#66738C",

                                maxTicksLimit: 8
                            },


                            grid: {

                                color:
                                    "rgba(35, 44, 61, 0.6)"
                            }
                        },


                        y: {

                            beginAtZero:
                                true,


                            ticks: {

                                color:
                                    "#66738C"
                            },


                            grid: {

                                color:
                                    "rgba(35, 44, 61, 0.6)"
                            }
                        }
                    }
                }
            }
        );
}


/* =========================================================
   UPDATE CHART DATA
   ========================================================= */

function updateCharts(backends) {

    if (
        !trafficChart ||
        !latencyChart
    ) {

        return;
    }


    const now =
        new Date();


    const currentTime =
        now.getTime();


    const timeLabel =
        now.toLocaleTimeString(
            [],
            {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit"
            }
        );


    let elapsedSeconds = 2;


    if (
        previousChartTime !== null
    ) {

        elapsedSeconds =
            (
                currentTime -
                previousChartTime
            ) / 1000;


        if (
            elapsedSeconds <= 0
        ) {

            elapsedSeconds = 2;
        }
    }


    previousChartTime =
        currentTime;


    chartHistory.labels.push(
        timeLabel
    );


    backends.forEach(
        backend => {

            const port =
                Number(
                    backend.port
                );


            if (
                !chartHistory.traffic[
                    port
                ]
            ) {

                chartHistory.traffic[
                    port
                ] = [];
            }


            if (
                !chartHistory.latency[
                    port
                ]
            ) {

                chartHistory.latency[
                    port
                ] = [];
            }


            const currentRequests =
                Number(
                    backend.total_requests || 0
                );


            const previousRequests =
                Number(
                    previousChartRequests[
                        port
                    ] || 0
                );


            const requestDifference =
                Math.max(
                    0,
                    currentRequests -
                    previousRequests
                );


            const requestsPerSecond =
                requestDifference /
                elapsedSeconds;


            chartHistory.traffic[
                port
            ].push(
                Number(
                    requestsPerSecond.toFixed(2)
                )
            );


            chartHistory.latency[
                port
            ].push(
                Number(
                    backend.avg_response_time ||
                    0
                )
            );


            previousChartRequests[
                port
            ] =
                currentRequests;

        }
    );


    if (
        chartHistory.labels.length >
        MAX_CHART_POINTS
    ) {

        chartHistory.labels.shift();


        [
            9001,
            9002,
            9003
        ].forEach(
            port => {

                chartHistory.traffic[
                    port
                ].shift();


                chartHistory.latency[
                    port
                ].shift();

            }
        );
    }


    trafficChart.data.labels =
        chartHistory.labels;


    trafficChart.data.datasets[
        0
    ].data =
        chartHistory.traffic[
            9001
        ];


    trafficChart.data.datasets[
        1
    ].data =
        chartHistory.traffic[
            9002
        ];


    trafficChart.data.datasets[
        2
    ].data =
        chartHistory.traffic[
            9003
        ];


    latencyChart.data.labels =
        chartHistory.labels;


    latencyChart.data.datasets[
        0
    ].data =
        chartHistory.latency[
            9001
        ];


    latencyChart.data.datasets[
        1
    ].data =
        chartHistory.latency[
            9002
        ];


    latencyChart.data.datasets[
        2
    ].data =
        chartHistory.latency[
            9003
        ];


    trafficChart.update(
        "none"
    );


    latencyChart.update(
        "none"
    );
}


/* =========================================================
   ALGORITHM SELECTOR
   ========================================================= */

function initializeAlgorithmSelector() {

    const selector =
        document.getElementById(
            "algorithm"
        );


    const label =
        document.getElementById(
            "algorithm-label"
        );


    const menu =
        document.getElementById(
            "algorithm-menu"
        );


    const options =
        document.querySelectorAll(
            ".algorithm-option"
        );


    /*
       Required elements not found
    */

    if (
        !selector ||
        !label ||
        !menu
    ) {

        console.warn(
            "Algorithm selector elements not found."
        );


        return;
    }


    /*
       ---------------------------------------------
       Open / close dropdown
       ---------------------------------------------
    */

    selector.addEventListener(
        "click",
        function (event) {

            /*
               If clicking an option,
               let the option handler deal with it.
            */

            if (
                event.target.closest(
                    ".algorithm-option"
                )
            ) {

                return;
            }


            event.stopPropagation();


            selector.classList.toggle(
                "open"
            );

        }
    );


    /*
       ---------------------------------------------
       Select algorithm
       ---------------------------------------------
    */

    options.forEach(
        function (option) {

            option.addEventListener(
                "click",
                async function (event) {

                    event.preventDefault();

                    event.stopPropagation();


                    const selectedAlgorithm =
                        option.dataset.algorithm;


                    /*
                       Already selected
                    */

                    if (
                        selectedAlgorithm ===
                        label.textContent.trim()
                    ) {

                        selector.classList.remove(
                            "open"
                        );


                        return;
                    }


                    try {

                        /*
                           Prevent double-click
                        */

                        option.disabled = true;


                        selector.classList.add(
                            "is-busy"
                        );


                        /*
                           Send algorithm change
                           to load balancer
                        */

                        const response =
                            await fetch(
                                ALGORITHM_URL,
                                {
                                    method:
                                        "POST",

                                    headers: {
                                        "Content-Type":
                                            "application/json"
                                    },

                                    body:
                                        JSON.stringify({
                                            algorithm:
                                                selectedAlgorithm
                                        })
                                }
                            );


                        const result =
                            await response.json();


                        /*
                           Check response
                        */

                        if (
                            !response.ok ||
                            !result.success
                        ) {

                            throw new Error(
                                result.message ||
                                [result.error, result.detail]
                                    .filter(Boolean)
                                    .join(": ") ||
                                "Failed to change algorithm"
                            );

                        }


                        /*
                           Update visible label
                        */

                        label.textContent =
                            result.algorithm;


                        /*
                           Update active option
                        */

                        options.forEach(
                            function (item) {

                                item.classList.remove(
                                    "active"
                                );

                            }
                        );


                        option.classList.add(
                            "active"
                        );


                        /*
                           Close dropdown
                        */

                        selector.classList.remove(
                            "open"
                        );


                        /*
                           Refresh dashboard
                           immediately
                        */

                        await fetchStats();


                        showToast(
                            `Routing algorithm changed to ${result.algorithm}`,
                            "success"
                        );


                    } catch (error) {

                        console.error(
                            "Algorithm change failed:",
                            error
                        );


                        showToast(
                            "Could not change routing algorithm: " +
                            describeError(error),
                            "error"
                        );


                    } finally {

                        option.disabled =
                            false;


                        selector.classList.remove(
                            "is-busy"
                        );

                    }

                }
            );

        }
    );


    /*
       ---------------------------------------------
       Close dropdown when clicking outside
       ---------------------------------------------
    */

    document.addEventListener(
        "click",
        function (event) {

            if (
                !selector.contains(
                    event.target
                )
            ) {

                selector.classList.remove(
                    "open"
                );

            }

        }
    );
}


/* =========================================================
   PAGE INITIALIZATION
   ========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    () => {

        console.log(
            "Vantora dashboard initialized"
        );


        console.log(
            `Stats endpoint: ${STATS_URL}`
        );


        /*
           Initialize charts
        */

        initializeCharts();


        /*
           Initialize algorithm dropdown
        */

        initializeAlgorithmSelector();


        enhanceAlgorithmSelectorAccessibility();


        /*
           Start live monitoring
        */

        startMonitoring();

    }
);

