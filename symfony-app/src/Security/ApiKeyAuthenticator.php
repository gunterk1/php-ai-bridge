<?php

declare(strict_types=1);

namespace App\Security;

use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Security\Core\Authentication\Token\TokenInterface;
use Symfony\Component\Security\Core\Exception\AuthenticationException;
use Symfony\Component\Security\Core\Exception\CustomUserMessageAuthenticationException;
use Symfony\Component\Security\Http\Authenticator\AbstractAuthenticator;
use Symfony\Component\Security\Http\EntryPoint\AuthenticationEntryPointInterface;
use Symfony\Component\Security\Http\Authenticator\Passport\Badge\UserBadge;
use Symfony\Component\Security\Http\Authenticator\Passport\Passport;
use Symfony\Component\Security\Http\Authenticator\Passport\SelfValidatingPassport;

/**
 * Shared-secret authentication for the write endpoints.
 *
 * Deliberately the simplest thing that is actually correct rather than a JWT
 * setup nobody asked for. Two details are worth pointing at:
 *
 * The comparison uses hash_equals, so it does not leak the key one byte at a time
 * through response timing. A string comparison here would be a textbook side
 * channel in code that otherwise looks fine.
 *
 * An empty configured key rejects everything instead of accepting everything. A
 * missing environment variable should fail closed; the opposite has silently
 * opened more endpoints than any exploit.
 *
 * It also serves as the firewall's entry point. Without that, a request carrying
 * no key at all never reaches supports(), and Symfony answers with its default
 * HTML error page — an HTML 401 to a JSON client is a parse error dressed up as
 * an auth failure, and it sends the caller looking in the wrong place.
 */
final class ApiKeyAuthenticator extends AbstractAuthenticator implements AuthenticationEntryPointInterface
{
    public function __construct(private readonly string $expectedKey)
    {
    }

    public function supports(Request $request): ?bool
    {
        return $request->headers->has('X-API-Key');
    }

    public function authenticate(Request $request): Passport
    {
        $provided = (string) $request->headers->get('X-API-Key', '');

        if ($this->expectedKey === '' || !hash_equals($this->expectedKey, $provided)) {
            throw new CustomUserMessageAuthenticationException('Invalid API key.');
        }

        return new SelfValidatingPassport(new UserBadge('service-client'));
    }

    public function onAuthenticationSuccess(Request $request, TokenInterface $token, string $firewallName): ?Response
    {
        return null;
    }

    public function onAuthenticationFailure(Request $request, AuthenticationException $exception): ?Response
    {
        return new JsonResponse(['error' => 'Unauthorized'], Response::HTTP_UNAUTHORIZED);
    }

    /** Reached when no credentials were supplied at all. */
    public function start(Request $request, ?AuthenticationException $authException = null): Response
    {
        return new JsonResponse(
            ['error' => 'Unauthorized', 'hint' => 'Send the shared secret in the X-API-Key header.'],
            Response::HTTP_UNAUTHORIZED,
        );
    }
}
