import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/investigate/$jobId')({
  component: RouteComponent,
})

function RouteComponent() {
  return <div>Hello "/investigate/$jobId"!</div>
}
