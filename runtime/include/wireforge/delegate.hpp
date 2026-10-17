/**
 * @file delegate.hpp
 * @brief Small non-owning callable wrapper that never allocates.
 */
#ifndef WIREFORGE_DELEGATE_HPP
#define WIREFORGE_DELEGATE_HPP

#include <type_traits>
#include <utility>

namespace wireforge {

/// @brief Declared only; see the specialization for function types.
template <typename Signature> class Delegate;

/**
 * @brief Non-owning reference to a free function, a member function bound to an object, or a
 *        callable object.
 *
 * The delegate stores two pointers and never allocates. Objects bound with bind() or
 * from_callable() are referenced, not copied: they must outlive the delegate.
 * @code
 * Delegate<int(int)> d1 = Delegate<int(int)>::from_function(&twice);
 * Delegate<int(int)> d2 = Delegate<int(int)>::bind<&Counter::add>(counter);
 * auto lambda = [](int value) { return value + 1; };
 * Delegate<int(int)> d3 = Delegate<int(int)>::from_callable(lambda);
 * @endcode
 * @tparam R Return type.
 * @tparam Args Parameter types.
 */
template <typename R, typename... Args> class Delegate<R(Args...)> {
public:
    /// @brief Free function type accepted by from_function().
    using Function = R (*)(Args...);

    /// @brief Creates an empty delegate; calling it is not allowed.
    constexpr Delegate() noexcept = default;

    /// @brief Refers to the free function @p function.
    [[nodiscard]] static constexpr Delegate from_function(Function function) noexcept {
        Delegate delegate;
        delegate.function_ = function;
        delegate.stub_ = function == nullptr ? nullptr : &call_function;
        return delegate;
    }

    /**
     * @brief Refers to member function @p Method called on @p object.
     * @tparam Method Pointer to a member function of T with this delegate's signature.
     */
    template <auto Method, typename T>
    [[nodiscard]] static constexpr Delegate bind(T& object) noexcept {
        Delegate delegate;
        delegate.object_ = &object;
        delegate.stub_ = &call_member<T, Method>;
        return delegate;
    }

    /// @brief Refers to the callable object @p callable (for example a lambda); it must be an
    /// lvalue.
    template <typename F>
    [[nodiscard]] static constexpr Delegate from_callable(F& callable) noexcept {
        static_assert(!std::is_const_v<F>, "bind a non-const callable object");
        Delegate delegate;
        delegate.object_ = &callable;
        delegate.stub_ = &call_callable<F>;
        return delegate;
    }

    /// @brief true when the delegate refers to something callable.
    [[nodiscard]] constexpr explicit operator bool() const noexcept { return stub_ != nullptr; }

    /**
     * @brief Calls the referenced function.
     * @pre The delegate is not empty.
     */
    R operator()(Args... args) const { return stub_(*this, std::forward<Args>(args)...); }

private:
    using Stub = R (*)(const Delegate&, Args...);

    static R call_function(const Delegate& self, Args... args) {
        return self.function_(std::forward<Args>(args)...);
    }

    template <typename T, auto Method> static R call_member(const Delegate& self, Args... args) {
        return (static_cast<T*>(self.object_)->*Method)(std::forward<Args>(args)...);
    }

    template <typename F> static R call_callable(const Delegate& self, Args... args) {
        return (*static_cast<F*>(self.object_))(std::forward<Args>(args)...);
    }

    void* object_ = nullptr;
    Function function_ = nullptr;
    Stub stub_ = nullptr;
};

} // namespace wireforge

#endif // WIREFORGE_DELEGATE_HPP
