function handler(event) {
    var request = event.request;
    var headers = request.headers;
    if (!headers.host || !headers.host.value) {
        return request;
    }

    var host = headers.host.value.toLowerCase();
    if (host === 'white-devel.com') {
        var redirectUrl = 'https://tournaments.white-devel.com' + request.uri;
        var querySuffix = buildQueryString(request.querystring);
        if (querySuffix) {
            redirectUrl += querySuffix;
        }

        return {
            statusCode: 301,
            statusDescription: 'Moved Permanently',
            headers: {
                location: { value: redirectUrl }
            }
        };
    }

    return request;
}

function buildQueryString(queryParams) {
    if (!queryParams) {
        return '';
    }

    var parts = [];
    for (var key in queryParams) {
        if (!Object.prototype.hasOwnProperty.call(queryParams, key)) {
            continue;
        }

        var param = queryParams[key];
        if (param.multiValue && param.multiValue.length > 0) {
            for (var i = 0; i < param.multiValue.length; i++) {
                var multi = param.multiValue[i];
                if (multi && multi.value !== undefined) {
                    parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(multi.value));
                }
            }
        } else if (param.value !== undefined) {
            parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(param.value));
        }
    }

    if (parts.length === 0) {
        return '';
    }

    return '?' + parts.join('&');
}
