-- post.lua
-- Script to test POST /todos endpoint

wrk.method = "POST"
wrk.path = "/todos"
wrk.headers["Content-Type"] = "application/json"

-- Function that generates the request body dynamically
counter = 0

request = function()
    counter = counter + 1
    local body = string.format('{"title": "Todo Item #%d"}', counter)
    return wrk.format("POST", wrk.path, wrk.headers, body)
end
