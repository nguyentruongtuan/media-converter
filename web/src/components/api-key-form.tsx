import { useState, type FormEvent } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, ApiError } from "@/lib/api"

export function ApiKeyForm({ onSubmit }: { onSubmit: (key: string) => void }) {
  const [value, setValue] = useState("")
  const [error, setError] = useState("")
  const [checking, setChecking] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setChecking(true)
    setError("")
    try {
      await api(value, "GET", "/v1/videos?limit=1")
      onSubmit(value)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That API key was rejected." : (err as Error).message)
      setChecking(false)
    }
  }

  return (
    <Card className="mx-auto w-full max-w-md">
      <CardHeader>
        <CardTitle>Enter your API key</CardTitle>
        <CardDescription>The key is kept in this browser's local storage.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="api-key">API key</Label>
            <Input
              id="api-key"
              type="password"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button type="submit" size="lg" disabled={checking || !value}>
            {checking ? "Checking..." : "Continue"}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}
